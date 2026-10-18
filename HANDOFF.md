# ZETRAX copybot — engineering handoff

> Audience: an AI engineer taking over this repo. **This file contains no secret values.** Secrets are referenced by
> variable name and location only. Never commit, print, log or paste secret values.

## 1. Goal and owner constraints (read first)

- **What it is:** a Solana memecoin **copy-trading bot in PAPER mode**. It picks "good" traders from public leaderboards and
  on-chain discovery, watches their wallets in real time, and **simulates** copying their buys and sells. Fills use real
  Jupiter quotes, with simulated latency and fees. Starting paper equity is $52.
- **Owner:** a beginner. Explain things plainly, keep changes safe and reversible, and make everything manageable **from
  Telegram first**. The owner should never need the server console.
- **Hard rules:**
  1. **Live trading is built but OFF, and the owner can switch it on.** On 2026-10-14 the owner approved in writing building
     the real-money module and linking a wallet, but not funding it until the strategy is proven; later the same day the
     owner asked in writing that the real-money option always stays available, off by default, and that he can turn it on
     himself. So there are two switches (`live.py`): `/live arm` (every SAFETY and STRATEGY check) and `/live trial` (the
     owner's capped override: SAFETY checks only, $10 positions, every buy approved by tap). Only the Trend Rider book can
     ever go live (book `trend-live`); copy and consensus books are paper-only in code. Never weaken the SAFETY checks
     (`Live.checks`), the signer's limits or its instruction rules without the owner's explicit written approval.
     `execution.py` stays a stub that always raises (unused).
  2. **Never ask for a seed phrase or private key**, and never write code that loads one.
  3. Never expose secrets: no echoing `.env`, and mask secrets in logs and Telegram (`…abcd`).
  4. Say "PAPER / NO real trades" visibly in status and trade messages.
- **Honest expectation:** the copy-trading backtest found no robust edge (see §11) and Trend Rider v1 failed its backtest
  (−37.5% over 2 years). Present results candidly. Don't hype.

## 2. Architecture and data flow

```
 candidate sources ──► selection.py (on-chain filters via Helius history) ──► top_n traders (kv 'selected')
  (fomoapi leaderboards,                                                       │ re-evaluated every 24h
   Dexscreener→buyers,                                                         ▼
   Birdeye, Nansen)                    wsmon.py  Helius WebSocket logsSubscribe(mentions=wallet)
                                       + fallback polling (getSignaturesForAddress)
                                                      │ new signature
                                                      ▼
                         chain.py parse_tx/classify (token delta vs SOL/USDC → buy/sell/xfer, USD size)
                                                      ▼
                         paper.py  Paper.on_buy / on_sell: safeguards → Jupiter quote → sleep latency →
                                   re-quote = our fill → fees/gas → sqlite (positions, trades, fills)
                                                      ▼
            notify.py (Telegram alerts, HTML via tgfmt.py)      dashboard/server.py (read-only web UI)
            CommandListener thread (long-poll /commands)        reads bot.db (read-only)
```

- A single process runs `main.py run` (systemd `copybot.service`). Inside it:
  - the main loop handles selection, wallet polling, marking equity every 5 min, the daily summary and the drawdown safeguard;
  - a `CommandListener` thread answers Telegram commands and has its own DB connection;
  - a `WalletStream` thread holds the websocket;
  - a heartbeat thread writes the heartbeat file every 30 s while the listener is alive.
- **Books (v4):** each strategy is a book with its own paper money, limits and kv keys `<book>:<name>` (`copy`, `consensus`,
  later `trend`, `trend-live`). Book rows live in the `b_*` tables (`b_positions`, `b_trades`, `b_fills`, `b_equity`,
  `b_signals`); the v1 tables (`positions`, `trades`, `fills`, `equity`, kv `cash`...) are left untouched so a `/rollback`
  to pre-books code resumes its old state instead of crashing on unknown columns. **Always insert by column name**
  (`db.ins`), never positionally. The copy book started fresh at v4 ($52) with the safety veto.
- State lives in **sqlite `bot.db`** (`db.py`). Tables: `kv`, `seen`, `decisions`, `positions`, `trades`, `equity`, `events`,
  and `fills` (created in `paper.py`). Main `kv` keys: `cash`, `peak`, `last_equity`, `selected`, `selected_at`, `shutdown`,
  `day_start`, `tg_chat_id`, `tg_offset`, `pending_update`, `pending_setkey`, `ws`, `last_poll`, `helius_calls`.

## 2b. Real-money module (built, OFF until the owner switches it on)

```
 TrendRunner signal ──► Live.propose ──► (arm: first 10 / trial: every buy → Telegram Approve/Reject) ──► Live.buy
     Jupiter /swap/v1/quote + /swap/v1/swap ──► unsigned tx ──► signer (Unix socket) ──► signed tx
     ──► kv live:pending_tx ──► Helius send, wait until confirmed / failed / expired ──► exact balance change from
         getTransaction ──► b_* tables, book 'trend-live' (live=1, txsig) ──► pending cleared
```
- **Signer** (`signer/signer.py`, `signer/solwire.py`): service `copybot-signer`, user `copybot-signer`, code root-owned in
  `/usr/local/lib/copybot-signer` (the bot cannot modify it), key `/var/lib/copybot-signer/wallet.json` (600, Solana CLI
  layout), socket `/run/copybot-signer/signer.sock` (group `copybot-signer`; the bot user is added to it; peer uid
  checked). Limits `/etc/copybot-signer.json` (root, installed only if missing; new keys default from `DEFAULTS`).
  - Instruction rules: 1 signer = the wallet; programs only ComputeBudget, System transfer **into the wallet's own wSOL
    account (or an account the same tx closes back to the wallet)**, Token sync / close **back to the wallet**, ATA
    create **for the wallet**, Jupiter v6; priority fee ≤ 300k lamports. Accounts from lookup tables can't be SOL
    destinations. `check_static` runs only these (self-test on an unfunded wallet).
  - Simulation rules: succeeds; the wallet's USDC/wSOL accounts (incl. their derived ATAs, measured even if created in
    the tx) keep owner and get no delegate; SOL and USDC move in opposite directions; loss ≤ 2.5% + fee allowance.
  - **Entries vs exits** (Reviewer 1): a **buy** (USDC→SOL) is capped at $20, $100 and 10 per UTC day, plus a daily fee
    budget (0.01 SOL). A **sell** (SOL→USDC) has no size cap and is not counted against the buy limits or the fee budget,
    so stops and `/panic` always work (sanity cap 30 sells a day; must leave 0.003 SOL for fees).
  - Withdrawals: SOL or USDC to the home address only. USDC goes to home's USDC ATA, created in the same tx if missing
    (no more swapping USDC→SOL to withdraw). Optional `amount` for the test withdrawal.
  - Pure Python + `python3-cryptography` (apt). `solwire.pda/ata` verified against Solana's own test vectors.
  - Console: `copybot wallet`, `copybot wallet-export` (prints the key after typing EXPORT), `copybot sethome`, `copybot signer`.
  - `cb_install_signer` writes `/var/lib/copybot/signer_update.json` when the signer code changes; the boot message shows it.
- **The one readiness policy** (`Live.checks`, shown by `/live` and the dashboard's Trend tabs, cached in kv `live:readiness`):
  - SAFETY (needed for arm and trial): signer running; home set (via `/sethome ADDR` → address shown in groups →
    `/sethome confirm CODE`); a test withdrawal the owner confirmed arriving (`/live testwithdraw` + code → `/live received`);
    ≥ $5 USDC and ≥ 0.02 SOL; `/live selftest` passed with a funded simulation in the last 24 h; no panic; no unresolved
    wallet mismatch; drawdown switch not tripped.
  - STRATEGY (arm only): the backtest of the CURRENT strategy version passed its gate; v2 on paper ≥ 7 days (kv
    `trend:v2_started`).
  - TIER 2 (display only, never automatic): raising the signer's limits is only worth discussing after 30+ paper days
    with a closed paper trade AND 20+ clean live round trips (Reviewer 2's two-tier idea).
  - Recommended (not blocking): key backed up offline (`/live backedup`).
- **Switching on:** `/live arm` or `/live trial` → code → same command + code. kv `live:armed` = {boot, until, mode}.
  It switches off by itself after `arm_days` (7; warned 24 h before), and on any restart (`boot_id`), `/panic`, wallet
  mismatch or the 6% drawdown switch. `/live off` switches off. Switching off only blocks NEW buys: positions keep their
  stops and exits. `/panic off` never switches live back on.
- **Unknown outcomes:** every signed swap is stored in kv `live:pending_tx` before sending. If the RPC can't say whether it
  landed, it stays there; new buys are blocked; retries re-send the SAME transaction until its blockhash expires; the
  main loop (`resolve_pending`) records it when it lands (message "recovered pending tx") or clears it when it expired.
  Reconcile settles pending txs before judging a mismatch.
- **Safety nets:** reconcile every 60 s (missing USDC/SOL → live off, kv `live:mismatch`, alert; `/live clear` + code
  resets records to the wallet after review); `/panic` (all books stop buying, live off, the live position is sold and
  retried every minute until sold, alert every 10 min while it fails); failed exits stay open, are retried every minute and
  shown in `/live` (a live position is never written off); `/withdraw` + code (all USDC, then all SOL, home).
- `/live selftest`: real Jupiter buy AND sell transactions through the signer's instruction checks (and simulation +
  limits when funded), plus three transactions the signer must REFUSE (SOL to a stranger, unknown program, someone else
  pays). Result in kv `live:selftest` {ok, full}.
- Not done on purpose: pinning the signer against `/update`. `/update` runs the repo's `update.sh` as root, so whoever
  controls the GitHub account controls the server anyway; the real protection is the GitHub account (2FA, private repo).
  Signer code changes are made visible in the boot message instead.

## 3. Files

| file | purpose |
|---|---|
| `main.py` | Entry point: `main.py run\|status\|summary\|select`. Main loop, Telegram command handler `reply()`, `/health` (`health_text`), `/update`, `/rollback`, live key test after `/setkey` (`test_key`), startup messages (run after the listener starts and wrapped so they can't crash the bot). |
| `chain.py` | Helius RPC client (`history`, `new_sigs`, `tx`), tx parsing and classification, round-trip reconstruction, Jupiter price/quote, token decimals, SOL/USD, fomoapi client. |
| `selection.py` | Trader evaluation filters and top-N pick. Writes every decision to the `decisions` table (shown on the dashboard). |
| `sources.py` | Candidate sources: fomo leaderboards, Dexscreener trending → recent on-chain buyers, Birdeye gainers, Nansen smart money. Dedupe, FOMO first. |
| `wsmon.py` | Helius websocket `logsSubscribe` per watched wallet. Status goes to kv `ws`. |
| `paper.py` | Paper broker: safeguards, sizing, the latency re-quote fill model, fees, PnL, the `fills` table with execution stats, the daily summary and the drawdown shutdown. |
| `live.py` | Real-money Trend Rider book: readiness checks, arm/trial/off, approvals, swaps through the signer with pending-tx tracking, exits, reconcile, panic, withdraw, test withdrawal, selftest. |
| `strategy_v2.py` | Trend Rider v2 rules (pure functions, shared by the backtest and the live runner). |
| `backtest_v2.py` | 3-coin daily backtest, benchmarks and the v2 gate. |
| `candles.py` | Daily candles for SOL/BTC/ETH (Coinbase, Kraken fallback), table `candles`. |
| `costs.py` | Measured SOL/USDC trading costs from real Jupiter quotes (kv `costs:samples`). |
| `trend.py` | `TrendRunner` (paper + live legs), `/backtest` job and report. |
| `report.py` | `/report`: every book's results since the start + all buys/sells as `zetrax-trades.csv` (`notify.send_document`). |
| `strategy_fast.py`, `backtest_fast.py`, `fastlab.py` | Faster candidates (research): rules, 1h backtest + gate, `/backtest fast` job and reports. |
| `tests/` | `test_signer.py`, `test_live.py` (fake chain + real signer), `test_v2.py` (rules, fills, replay = backtest). |
| `signer/` | The wallet signer service (see 2b), its systemd unit and default limits; installed by `cb_install_signer`. |
| `execution.py` | **Legacy stub, unused. Always raises `LiveTradingDisabled`.** Requires `live_trading: true` AND a `LIVE_CONFIRM` file, and even then it is not implemented. Do not change without explicit user approval. |
| `notify.py` | `Notifier.send` (HTML with a plain-text fallback, never raises), `CommandListener` (long-poll, chat allowlist, `setMyCommands`, `/setkey` and `/keys`), `heartbeat()`, `ALLOWED_KEYS`, `mask()`. |
| `tgfmt.py` | Telegram look: `term(cmd, rows, tag, extra)` builds the `root@zetrax:~$ cmd` header plus a `<pre>` block. Also `kv()` (aligned, wraps at 32 cols), `esc()`, `short()`, `usd()`, `plain()`, `COMMANDS` (the menu). |
| `db.py` | sqlite wrapper and schema. |
| `migrate_config.py` | Merges new default config keys into the user's `config.yaml` (keeps user values, saves a backup). |
| `config.yaml` | Strategy and safety parameters (§4). |
| `dashboard/server.py`, `index.html` | Read-only "hacker terminal" dashboard on 127.0.0.1:8787 behind Caddy. Shows `/api/state` (equity curve, positions with live Jupiter prices, trades, decisions, exec stats). Token-gated by `?t=`. |
| `dashboard/update_menu.py` | Sets the Telegram chat menu button to the dashboard URL. |
| `install.sh` | One-shot server install (packages, Caddy, ufw, user, venv, `.env` prompt, units, helpers, sudoers, watchdog). |
| `update.sh` | Self-healing update (§7). |
| `system/copybot-selfupdate` | Root helper for `/update`. Runs a detached `systemd-run`: download the `main` tarball → `update.sh`. |
| `system/copybot-setkey` | Root helper for `/setkey`. Reads NAME and VALUE from stdin and writes them to `.env`. |
| `system/copybot-rollback` and `copybot-rollback.sh` | Root helper for `/rollback` (detached) and the rollback script. |
| `system/copybot-lib.sh` | Shared root-side bash library: Telegram send with masking (independent of the bot code), snapshot/restore, health check, status file. |
| `system/copybot-watchdog.{sh,service,timer}` | Root watchdog, every 2 min. |
| `system/*.sudoers` | Narrow sudoers rules: user `copybot` may run each helper **with no arguments only**. |
| `systemd/copybot.service`, `copybot-dashboard.service` | Service units (User=copybot, Restart=always). |

## 4. config.yaml and strategy

- **Mode:** `mode: paper` (asserted at startup). `live_trading: false` is forced back to false by `update.sh` every time.
- **Paper sizing and costs (`paper`):**
  - `start_equity_usd` = 52.
  - Size = `min(size_multiplier`(0.25) × trader USD size, `max_position_pct`(0.20) × equity, available cash)`. Skip if below `min_order_usd`(1).
  - `max_open_positions` = 6. A trader adding to a token we already hold is ignored.
  - `fee_pct` = 0.02 per side (copyfomo-like; 0.005 ≈ FOMO-like) plus `gas_usd` = 0.01 per side.
  - **Fill model:** first Jupiter quote at decision time (`quote_slippage_bps` = 300) → sleep `exec_latency_s` (1.5 s) → **second
    quote = our fill**. `slippage_pct` adds an extra haircut on top (default 0).
  - Every fill is recorded with detect latency (trader block time → our detection), total latency, slippage vs the trader's price
    in bps, price impact, and failures.
- **Risk gate (`paper.Paper.on_buy`, settings `gate:`):** every buy is a proposal; first failure wins and is logged to
  `b_signals` with its reason. Order: panic / book off / paused / drawdown / daily loss → stale signal (trade seen > 120 s
  late) → already holding → max positions → size (book cap, cash, **15% of all books' money per coin**) → market storm (SOL
  −5% in 1 h pauses all books 60 min) → liquidity → **safety veto** (`veto.py`: mint authority, freeze authority,
  Token-2022 fee/hook/permanent delegate, top-10 real holders > 30%; pools/curves/programs detected as off-curve owners and
  excluded; network errors refuse) → quotes + impact → **exit check** (buy then instant sell may lose ≤ 6%; it shows an
  exit exists now and what it costs, not that the coin stays sellable). Sells and
  exits are never gated.
- **Trend Rider v2 (`trend` paper book, `trend-live` real money), SOL/USDC, daily candles.** Rules in `strategy_v2.py`,
  fixed 14 Oct 2026 before any v2 result: once a day after the 00:00 UTC close; uptrend = close > SMA50 and SMA20 > SMA50;
  entry = close above the highest close of the previous 20 days, filled at the next open (skipped if that is > 5% above
  the signal close); stop = signal close − 3×ATR14 (Wilder), trailing: highest close since entry − 3×ATR14, never down,
  watched all day; trend exit = a close under SMA50, sold at the next open; size = 1% of the book at risk counting both
  costs and a 1% gap buffer, max 50% of the book and the live per-swap limit; skip trades whose expected costs exceed 25%
  of the risk budget. `trend.py` evaluates the new daily close at 00:05 UTC (exits/trailing first, then entries; no entry
  if > 6 h late), and checks stops against the live Jupiter price every ~30 s. Paper book limits: 2% day, 4% week, 10%
  drawdown (the live book stops at 6%). v1 (15m) was retired after failing its backtest; its open positions are closed.
- **Backtest (`backtest_v2.py`, `/backtest`):** daily SOL, BTC, ETH from June 2021 (Coinbase), costs = max(0.15%,
  measured p90 per side from real $20 Jupiter round trips), next-open fills, gap fills at the open, doubled-cost rerun,
  first 75% training / last 25% held back. Gate v2: ≥ 40 trades across the 3 coins; PF ≥ 1.3 with costs doubled; profitable
  on ≥ 2 of 3 coins; **SOL alone profitable with costs doubled** (added from Reviewer 1 before any result); worst coin
  drawdown ≤ 10%; held-back period profitable; still profitable without each coin's best trade. The report shows each
  coin, the held-back period, buy-and-hold, and notes (coins are correlated; the held-back period overlaps prices the v1
  test saw). kv `backtest:v2` (v1's result stays in `backtest:last`, so a `/rollback` to v1 never reads a v2 pass as its own),
  `backtest:holdout_views` per version.
  `tests/test_v2.py` replays candles day by day through the live paper runner: same entries, same exit reasons.
- **Faster candidates (research only, `strategy_fast.py`, `backtest_fast.py`, `fastlab.py`, `/backtest fast`).** The owner
  asked on 18 Oct 2026 for a strategy that trades much more often (his target: about 22 trades a month). Two candidates,
  rules fixed before any result, on 1h candles of SOL/BTC/ETH with the same daily regime filter (last completed day's
  close > SMA50): A "Dip Buyer" (RSI(2) < 10 on 1h → buy next open; exit on a close above the 5h SMA, 48 h, regime off;
  stop 3×ATR14 1h) and B "Fast Trend" (v2 on 4h bars). Risk 0.5% per trade. Gate "fast": ≥ 300 trades; PF ≥ 1.3 at
  0.15%/side; still profitable with costs doubled (all coins and SOL alone); ≥ 2 of 3 coins profitable; SOL profitable
  in training AND held-back; worst coin DD ≤ 10%; held-back profitable; profitable without each coin's 10 best trades.
  Result in kv `backtest:fast`. Nothing trades from this: a passing candidate gets a paper book first (separate PR),
  real money only after that and with the owner's OK.
- **Consensus book:** when 2+ watched traders buy the same coin within 15 min (`books.consensus.window_minutes`), the
  consensus book buys 10% of its $25; a sell by any of those traders sells it; global exits apply.
- **Safeguards:**
  - `daily_loss_limit_pct` 0.15: pause new buys for the rest of the UTC day.
  - `max_drawdown_pct` 0.40: permanent shutdown of new buys (kv `shutdown`).
  - `min_liquidity_usd` 50 000 (Jupiter).
  - `max_price_impact_pct` 0.03.
  - `stop_file: STOP`: create `/opt/copybot/STOP` and the bot exits its loop.
- **Own exits (`exits`, paper):** checked every `check_seconds` (30) from the main loop's drain loop with one batched
  Jupiter price call (`chain.jup_prices`). Per open position, value vs remaining cost (buy fee included):
  `stop_loss_pct` 0.15 → sell all; `take_profit_pct` 0.30 → sell all; `max_hold_hours` 24 → sell all. Exit sells use the same
  quote → latency → re-quote fill as copied sells (`Paper._sell`) and are tagged in `fills.reason`
  (`trader_sold|stop_loss|take_profit|max_hold`, older rows NULL = trader). `enabled: false` restores pure copying.
- **Failed sells retry:** a sell with no Jupiter route is queued in kv `pending_sells` and retried with backoff
  (30 s doubling to 10 min). Telegram alerts once on the first failure. After `sell_retry_give_up_hours` (24) the position
  is **written off at $0** (`fills.status='writeoff'`, `reason='write_off'`), so equity never keeps a dead token at cost.
- **Selection (`selection`):**
  - Candidates come from the `leaderboards` [24h, 7d, 30d, all] (limit 150) plus the other sources, capped at `max_candidates` 300.
  - Each candidate needs a Solana share ≥ 0.5.
  - The wallet's last `lookback_days` (30) of history is pulled from Helius, and closed round trips ≥ `min_trip_cost_usd` ($5) are built.
  - To pass, a wallet needs:
    - trades ≥ 10, realized PnL > 0, profit factor ≥ 1.2, win rate ≥ 30%;
    - max drawdown ≤ 50% of gross wins;
    - top-3 wins ≤ 70% of gross wins;
    - ≤ 20 closed trades/day (anti-bot).
  - The top 3 passers by PnL are picked, re-evaluated every 24 h.
  - Helius budget: `max_helius_credits_per_run` 20 000. History pages: 15 for FOMO candidates, 8 for others.
- **Monitor:** websocket at `confirmed` commitment. Fallback polling every 25 s while the websocket is down, and every 120 s
  while it is up (deduped by signature).
- **Sources:** fomo on, dexscreener_buyers on (20 tokens / 60 wallets), birdeye on (windows 1W, 30d; skipped if no key), nansen off.
- **Config migration:** new keys must be added to the repo `config.yaml`. `migrate_config.py` merges them into the user's file on update.

## 5. Data sources and APIs

All keys live in **`/opt/copybot/.env`** (owner root:copybot, mode 640) and are loaded by systemd `EnvironmentFile`.

| service | used for | endpoint(s) | env var | limits / notes |
|---|---|---|---|---|
| Helius (free plan) | wallet history, tx fetch, websocket, decimals, `getSlot` key test | `https://mainnet.helius-rpc.com/?api-key=…`, `wss://mainnet.helius-rpc.com/?api-key=…` (`getTransactionsForAddress`, `getSignaturesForAddress`, `getTransaction`, `logsSubscribe`) | `HELIUS_API_KEY` | ~1M credits/month on the free plan; the code counts ~10 credits/call and caps 20k per selection run. Watch the RPS. |
| fomoapi.io | trader leaderboards (handles plus wallets) | `https://api.fomoapi.io/v2/leaderboard/{24h\|7d\|30d\|all}?limit=N` (Bearer) | `FOMOAPI_KEY` | 250 credits per leaderboard call, so ~1000 per daily refresh. The key test costs 250. |
| Birdeye (free) | gainers/losers traders; price for the key test | `https://public-api.birdeye.so/trader/gainers-losers?…`, `/defi/price` (headers `X-API-KEY`, `x-chain: solana`) | `BIRDEYE_API_KEY` | Free tier is low-RPS. The source is skipped if the key is absent. |
| Jupiter | prices, liquidity, quotes (the fill model) | `https://api.jup.ag/price/v3?ids=…`, quote API under `JUP_BASE` (default `https://api.jup.ag`) | `JUPITER_API_KEY` (optional), `JUP_BASE` | Keyless ~0.5 RPS; a free key gives ~1 RPS. |
| Dexscreener | trending tokens → recent buyers (via Helius) | `https://api.dexscreener.com/token-boosts/latest/v1`, `/token-boosts/top/v1` | none | Public; roughly 60 req/min on these endpoints. |
| Nansen | smart-money DEX trades (disabled) | `https://api.nansen.ai/api/v1/smart-money/dex-trades` | `NANSEN_API_KEY` | Free plan has only 10 credits/day. |
| Telegram Bot API | alerts and commands | `https://api.telegram.org/bot<token>/…` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (also saved as kv `tg_chat_id`) | Only the saved chat is answered. The first `/start` binds it if unset. |

- **Rotating a key:** send `/setkey NAME VALUE` in Telegram.
  - NAME must be in `ALLOWED_KEYS` in `notify.py` (BIRDEYE, JUPITER, NANSEN, HELIUS, FOMOAPI).
  - The bot deletes the message first and validates the value against `^[A-Za-z0-9._:-]{8,200}$`.
  - It passes the value via stdin to the root helper `copybot-setkey`, which rewrites `.env`, then restarts.
  - After the restart, `test_key()` makes a cheap live call and reports OK or FAILED (the value shown masked).
  - `/keys` lists which keys are set, masked.
- `GITHUB_TOKEN` (optional, fine-grained, Contents read-only on this repo only) lets `/update` work with a **private** repo.
  Root reads it from `.env`; `copybot-selfupdate` and `cb_gh_sha` pass it to curl via a root-only header file (`-H @file`),
  never on a command line. Without it, downloads are anonymous as before; a wrong token falls back to anonymous.
- `TELEGRAM_BOT_TOKEN` is intentionally **not** settable via `/setkey`. Change it in the console (`.env`) only.

## 6. Deployment (Vultr)

- **Server:** Vultr VPS, public IP `45.63.99.138`, Ubuntu, root SSH or Vultr web console. The root password is held by the
  owner only. Never ask for it in chat, and never store it.
- **Dashboard:** `https://45-63-99-138.sslip.io/`. Caddy terminates TLS (Let's Encrypt via sslip.io) and reverse-proxies to
  127.0.0.1:8787. Access needs `?t=<dashboard token>`. The token is in `/opt/copybot/dashboard/token.txt` (600), and the base
  URL is in `dashboard/url.txt`. `/dashboard` in Telegram or `copybot url` on the server gives the full link.
- **Firewall (ufw):** allow 22, 80, 443 only.
- **Paths:**
  - `/opt/copybot` holds the code, the venv (`.venv`), `.env`, `bot.db`, `bot.log`, `VERSION`, `heartbeat`, `bot.pid`, `STOP`.
  - `/var/lib/copybot` (755) holds `update_status.json`, `last_snapshot.json`, `watchdog/` state and `update.lock`.
  - `/var/backups/copybot` (700, root) holds code snapshots (`<ts>/code.tgz`, `VERSION`) plus a `last` symlink. The last 3 are kept.
  - `/usr/local/lib/copybot/{lib.sh,rollback.sh,watchdog.sh}` and `/usr/local/sbin/copybot-{selfupdate,setkey,rollback}` are the root-side helpers.
  - Logs: `/var/log/copybot-update.log`, `/var/log/copybot-rollback.log`, and `journalctl -u copybot`.
- **Users:** services run as the system user `copybot` (no shell); `/opt/copybot` is owned by copybot. The bot can only
  escalate through the three sudoers rules in `/etc/sudoers.d/copybot-{update,setkey,rollback}`, each with no arguments
  allowed and each validated with `visudo -cf` before install.
- **systemd:**
  - `copybot.service`: `main.py run`, `EnvironmentFile=.env`, Restart=always 10 s.
  - `copybot-dashboard.service`.
  - `copybot-watchdog.timer` + `.service` (root oneshot, every 2 min).
- **CLI (`/usr/local/bin/copybot`, written by update.sh):** `copybot status|start|stop|restart|logs|url|update|rollback|health|version`.

## 7. Update / rollback / watchdog / heartbeat pipeline

- **`/update` (Telegram)** → `sudo -n copybot-selfupdate` → detached `systemd-run` → resolves the `main` SHA → downloads the
  tarball (with `GITHUB_TOKEN` via the API tarball endpoint when set, else the public archive) → runs **the new** `update.sh`.
  `copybot update` (root CLI) runs the same helper with `CB_FOREGROUND=1`, so there is one download path. The helper must stay
  self-contained: rollback restores `/opt/copybot` only, never `/usr/local/sbin`. Output goes to
  `/var/log/copybot-update.log`. Because the new `update.sh` runs, changes to it take effect on the same update; changes to
  the helper itself take effect from the next update.
- **`update.sh` steps** (holds `flock` on `/var/lib/copybot/update.lock`):
  1. **Pre-flight in a staging copy** using `/opt/copybot/.venv`:
     - pip install requirements;
     - `py_compile` all `*.py` and `dashboard/*.py`;
     - migrate the user's config;
     - import every module with `COPYBOT_NO_TELEGRAM=1`.

     On failure it sends a Telegram "ABORTED, nothing changed" and exits.
  2. **Snapshot** the current code (excludes `.env`, `bot.db*`, logs, `.venv`, `heartbeat`, `token.txt`, `url.txt`).
  3. **Install:** `cp "$SRC"/*.py`, then the dashboard, units, `system/`, config migration, `VERSION`, root helpers, sudoers,
     watchdog timer and CLI. An `ERR` trap rolls back on any failure.
  4. **Restart + health check (≤ 90 s):** the service must be active **and** `heartbeat` must be newer than the restart, then
     still be active after a settle period.
  5. **Unhealthy:** restore the snapshot, clear kv `pending_update`, restart, and send a plain Telegram message straight from
     bash/python using `.env`, with a masked journal tail. On success, the bot itself confirms "updated to X" via kv
     `pending_update`.
- **Lesson (tgfmt.py incident):** `update.sh` used to copy a *hardcoded* file list. Adding `tgfmt.py` caused an
  `ImportError` crash loop and a dead Telegram listener, which the user could only fix from the console. It now copies
  `*.py`, verifies every module is installed, and pre-flights imports. **Any new module or dir must be covered by update.sh.**
  Check this whenever you add files.
- **Heartbeat:** `notify.heartbeat()` writes `/opt/copybot/heartbeat` (overridable with `COPYBOT_HEARTBEAT`). It is written
  every 30 s by a thread while the CommandListener is alive (or while Telegram is intentionally off), and also on every
  listener poll.
- **Watchdog** (`watchdog.sh`, root, every 2 min; skips if the update lock is held):
  - **Unhealthy means** the unit is failed or auto-restarting, NRestarts went up by 2 or more, or the heartbeat is more than
    5 min old while uptime is more than 5 min.
  - **Alerts** go out at most once per 30 min.
  - **Auto-rollback** happens once per version, when the failure began ≤ 30 min after an `ok` update and a snapshot exists.
  - **Recovery:** it sends one "recovered" message.
  - A deliberate `copybot stop` (inactive, Result=success) does not alert.
- **`/rollback`** → `sudo -n copybot-rollback` → detached `rollback.sh`: restore `last`, restart, health check, Telegram result.
  Rate-limited to once per 120 s.
- **`/health`** shows version, uptime (bot.pid mtime), heartbeat age, watchdog state, last update result/to/from/when (with
  masked detail), and the snapshot version.

## 8. Telegram commands

Answered only for the saved chat id. Replies are HTML (`tgfmt.term`) with a plain fallback.

- `/books`: every book's money, P&L, open positions and state. `/book NAME on|off` pauses or resumes a book's new buys.
- `/signals`: the last 8 gate decisions with the reason.
- `/trend`: Trend Rider v2 state (close, 20/50-day averages, 20-day high, trend, signal, position with stop, last
  backtest verdict). `/backtest` runs the 3-coin backtest in a background thread; `/backtest last` repeats the report.
- `/start`: binds the chat if none is saved, then shows status, the dashboard link and help.
- `/status`: equity, cash, open positions, sells, realized PnL, exec stats (detect/fill medians, slippage, failures),
  websocket up/down, watched traders, shutdown flag.
- `/dashboard`: tokenized link. `/version`: running commit.
- `/update`: self-update, rate-limited to 120 s. `/rollback`: restore the last snapshot. `/health`: as above.
- `/keys`: masked keys. `/setkey NAME VALUE`: message deleted, allowlisted.
- `/live` (status + the full checklist), `/live arm` / `/live trial` (+ code), `/live off`, `/live selftest`,
  `/live testwithdraw` (+ code), `/live received`, `/live clear` (+ code), `/live backedup`, `/wallet` (address, balances,
  limits), `/sethome ADDRESS` → `/sethome confirm CODE` (once), `/panic` / `/panic off`, `/withdraw` (+ code).
- `/help`: the command list.
- The command menu is set by `setMyCommands` from `tgfmt.COMMANDS` when the listener starts.
- **Push messages:**
  - lifecycle: boot, update confirm/rollback, key test, STOP shutdown;
  - trading: selection change, paper BUY/SELL/SELL-failed, skipped copy, daily summary, drawdown safeguard;
  - watchdog alerts and recovery notices.

## 9. Dashboard

- Single-page terminal-style UI (`index.html`) polling `/api/state?book=<book>` (token in the `x-token` header); a tab per
  book. Skip reasons come from `b_signals` (refused proposals).
- It opens `bot.db` **read-only** and shows:
  - equity curve and max drawdown;
  - open positions marked with live Jupiter prices, and unrealized PnL;
  - trades and win/loss counts;
  - selection decisions with their reasons and sources;
  - exec stats.
- Trend tabs show the real-money checklist (kv `live:readiness`) and the v2 backtest table (per coin, costs x2, held-back,
  just holding, gate checks, notes). The top tag says REAL MONEY on the `trend-live` tab.
- v2 adds: a copy-trading checklist (8 pass/fail checks, thresholds in `READY` in `server.py`), closed round trips with
  trader-vs-our return ("copy gap"), skip-reason counts, today's loss vs the daily limit, heartbeat age, chart ranges.
  Limits are read from `config.yaml` with a plain-text parser (the dashboard runs on the system python3, no PyYAML).
- Auth: the page itself (`/`) holds no data; `/api/state` needs the token as `?t=` or an `x-token` header. The page
  stores the token in localStorage and strips it from the address bar. Responses carry CSP/nosniff headers.
- The token comparison uses `hmac`. Screenshots in the parent research folder (`dash_iphone.png`, etc.) show the intended
  look on iPhone, iPad and laptop.

## 10. Testing approach

- `python3 -m py_compile *.py dashboard/*.py signer/*.py` on every change, then `python3 tests/test_signer.py`,
  `python3 tests/test_live.py` and `python3 tests/test_v2.py` (no network, no keys, no money; all must pass).
- **Message templates:** render with sample data by monkeypatching `Notifier.send` and `CommandListener`, then running
  `main.run()` with `stop_requested=lambda: True`. Run the real handlers and `Paper.on_buy/on_sell` with mocked
  `jup_price`, `jup_quote` and `decimals`. Assert that no line inside `<pre>` exceeds 32 chars and that dynamic values are
  HTML-escaped (try a handle like `<script>`).
- **Update pipeline:** simulate with env overrides `CB_TEST=1 CB_APP CB_BACKUPS CB_STATE CB_LIBDIR CB_TG_API CB_SETTLE
  CB_HEALTH_TIMEOUT`, a PATH shim for `systemctl`/`journalctl` (the shim must close fd 9 and stdin for the child), a fake
  Telegram HTTP server, and a fake `/opt`. Cover four cases:
  - a healthy update;
  - an import-broken commit (pre-flight abort, app unchanged);
  - a runtime crash (rollback, masked alert);
  - watchdog cases (auto-rollback, alert-only, rate limit, recovery, stale heartbeat).

  Also test under `LANG=C`: emoji and byte-cut tails once crashed the alert sender.
- Never point tests at the real chat or spend paid credits. Use a fake token or dummy keys, and a far-future `selected_at`
  to skip selection.

## 11. Backtest findings and known limitations

- Source: `BACKTEST.md` in the parent research folder (`/workspace/copybot-live`). 17 FOMO leaderboard wallets, 1,322
  reconstructed round trips, Dec 2025 → Oct 2026.
- Results by strategy:
  - **"Switch after 2 losses" (b) was the worst:** ~$4–5 from $52, about −92% drawdown in every scenario. It churns into new
    leaders right after their hot streaks.
  - **Chase the 7-day leader (a)** loses in all scenarios.
  - **The evidence filter (c)**, which is what the live selection is based on, roughly preserves capital at low costs but
    still loses.
  - **Top-3 qualified (d)** looks great, but almost all of it comes from one trader. Without that trader it ends at $25. Not robust.
- **Leaderboard ranks are driven largely by unrealized gains** (open bags marked up), not realized, repeatable PnL. That
  is why selection recomputes *realized* round trips on-chain instead of trusting the rank.
- **Biases:** survivorship / look-ahead (today's leaderboard wallets), optimistic fills, truncated histories, small samples.
  Fees matter a lot (2% vs 0.5%).
- **Limitations:**
  - Round-trip reconstruction ignores multi-token txs and transfers-in without a buy.
  - The paper fill uses a quote after a fixed latency, not true on-chain landing; MEV and failed txs are only approximated.
  - Jupiter keyless rate limits can cause quote failures, which are recorded as fill failures.
  - The free-tier Helius budget limits the candidate count.
  - The single-process sqlite design is fine at this scale.

## 12. Open TODOs

- Collect a few weeks of paper data and compare the live exec stats (detect latency, slippage vs trader) with the backtest
  assumptions.
- Out-of-sample selection check: record the picks and evaluate them forward, not on today's leaderboard.
- Compare exits vs pure copying using the dashboard's "why trips closed" panel before changing the thresholds.
- Optional: per-trader stop (pause a trader after N losing copies, with care, since the 2-loss rule backtested worst),
  token blacklist. (Take-profit / stop-loss simulation is done, see §4.)
- Optional: Jupiter free key for 1 RPS; Birdeye key for more candidates.
- Done: the chat id is no longer hardcoded (`install.sh` takes `TELEGRAM_CHAT_ID`, the existing `.env`, or a prompt;
  blank binds on first `/start`). The dashboard now shows heartbeat age; full `/health` data is still optional.
- Live trading: built (§2b). Before the owner funds the wallet: run `/backtest` for v2 and read it honestly; if it fails,
  don't tweak the rules until it passes. Raising the signer's limits needs Tier 2 evidence AND the owner's written OK.
- Later, maybe: USDC lending yield for idle cash (needs a new program in the signer allow-list → owner's written OK).

## 13. Rules for safe changes

1. Work on a **branch** and open a **PR** to `main` (github.com/energyisbackff-byte/copybot). The user deploys by sending
   `/update`, which pulls `main`, so whatever lands on `main` ships.
2. Keep `execution.py` locked and `mode: paper`. Don't touch `LIVE_CONFIRM` logic. Keep the paper wording in messages.
3. Telegram: build messages with `tgfmt.term`/`kv`. Keep lines **≤ 32 chars inside `<pre>`** (iPhone). **Escape every
   dynamic value** (`term` escapes rows; anything passed as `extra` must be pre-escaped with `esc()`). Sends must never raise.
4. New files: make sure `update.sh` installs them, the pre-flight imports them, and `install.sh` covers them.
5. New root helpers: no-argument sudoers rule only, validated with `visudo -cf`, installed by `update.sh`, input via stdin.
6. Never log or send secret values. Use `mask()` and `cb_mask`. Don't widen `/setkey` to arbitrary names.
7. Don't break the startup order: the listener and heartbeat start first, and startup notices are wrapped in try/except.
8. New config keys go in the repo `config.yaml` (migration adds them for the user).
9. Test as in §10 before merging. After deploy, check `/health`.

## 14. If Claude needs new APIs

- Claude does **not** get existing key values and should not ask for them. Code reads keys from the environment
  (`os.environ[...]`), and the user manages them.
- To add a new service:
  1. Add the env var name to `ALLOWED_KEYS` in `notify.py`, and a cheap live check in `main.test_key()`.
  2. Make the code skip gracefully if the key is absent.
  3. Merge via PR. The user runs `/update`, then sends `/setkey NEW_SERVICE_API_KEY <value>` in Telegram. The message is
     deleted and the value goes straight into `.env` via the root helper.
- If a value doesn't match the validator regex (`[A-Za-z0-9._:-]{8,200}`), adjust the validator conservatively in the PR.

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
  1. **Paper only.** Real trading is LOCKED. Do not implement or enable live execution until the user explicitly approves it
     in writing. `execution.py` is a stub that always raises. Keep it that way.
  2. **Never ask for a seed phrase or private key**, and never write code that loads one.
  3. Never expose secrets: no echoing `.env`, and mask secrets in logs and Telegram (`…abcd`).
  4. Say "PAPER / NO real trades" visibly in status and trade messages.
- **Honest expectation:** the backtest found no robust edge (see §11). Present results candidly. Don't hype.

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
- State lives in **sqlite `bot.db`** (`db.py`). Tables: `kv`, `seen`, `decisions`, `positions`, `trades`, `equity`, `events`,
  and `fills` (created in `paper.py`). Main `kv` keys: `cash`, `peak`, `last_equity`, `selected`, `selected_at`, `shutdown`,
  `day_start`, `tg_chat_id`, `tg_offset`, `pending_update`, `pending_setkey`, `ws`, `last_poll`, `helius_calls`.

## 3. Files

| file | purpose |
|---|---|
| `main.py` | Entry point: `main.py run\|status\|summary\|select`. Main loop, Telegram command handler `reply()`, `/health` (`health_text`), `/update`, `/rollback`, live key test after `/setkey` (`test_key`), startup messages (run after the listener starts and wrapped so they can't crash the bot). |
| `chain.py` | Helius RPC client (`history`, `new_sigs`, `tx`), tx parsing and classification, round-trip reconstruction, Jupiter price/quote, token decimals, SOL/USD, fomoapi client. |
| `selection.py` | Trader evaluation filters and top-N pick. Writes every decision to the `decisions` table (shown on the dashboard). |
| `sources.py` | Candidate sources: fomo leaderboards, Dexscreener trending → recent on-chain buyers, Birdeye gainers, Nansen smart money. Dedupe, FOMO first. |
| `wsmon.py` | Helius websocket `logsSubscribe` per watched wallet. Status goes to kv `ws`. |
| `paper.py` | Paper broker: safeguards, sizing, the latency re-quote fill model, fees, PnL, the `fills` table with execution stats, the daily summary and the drawdown shutdown. |
| `execution.py` | **Live execution stub. Always raises `LiveTradingDisabled`.** Requires `live_trading: true` AND a `LIVE_CONFIRM` file, and even then it is not implemented. Do not change without explicit user approval. |
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

- `/start`: binds the chat if none is saved, then shows status, the dashboard link and help.
- `/status`: equity, cash, open positions, sells, realized PnL, exec stats (detect/fill medians, slippage, failures),
  websocket up/down, watched traders, shutdown flag.
- `/dashboard`: tokenized link. `/version`: running commit.
- `/update`: self-update, rate-limited to 120 s. `/rollback`: restore the last snapshot. `/health`: as above.
- `/keys`: masked keys. `/setkey NAME VALUE`: message deleted, allowlisted.
- `/help`: the command list.
- The command menu is set by `setMyCommands` from `tgfmt.COMMANDS` when the listener starts.
- **Push messages:**
  - lifecycle: boot, update confirm/rollback, key test, STOP shutdown;
  - trading: selection change, paper BUY/SELL/SELL-failed, skipped copy, daily summary, drawdown safeguard;
  - watchdog alerts and recovery notices.

## 9. Dashboard

- Single-page terminal-style UI (`index.html`) polling `/api/state?t=…`.
- It opens `bot.db` **read-only** and shows:
  - equity curve and max drawdown;
  - open positions marked with live Jupiter prices, and unrealized PnL;
  - trades and win/loss counts;
  - selection decisions with their reasons and sources;
  - exec stats.
- v2 adds: a go-live checklist (8 pass/fail checks, thresholds in `READY` in `server.py`), closed round trips with
  trader-vs-our return ("copy gap"), skip-reason counts, today's loss vs the daily limit, heartbeat age, chart ranges.
  Limits are read from `config.yaml` with a plain-text parser (the dashboard runs on the system python3, no PyYAML).
- Auth: the page itself (`/`) holds no data; `/api/state` needs the token as `?t=` or an `x-token` header. The page
  stores the token in localStorage and strips it from the address bar. Responses carry CSP/nosniff headers.
- The token comparison uses `hmac`. Screenshots in the parent research folder (`dash_iphone.png`, etc.) show the intended
  look on iPhone, iPad and laptop.

## 10. Testing approach

- `python3 -m py_compile *.py dashboard/*.py` on every change.
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
- Live trading: **out of scope** until the user explicitly approves. That would need a separate design review (key
  custody, limits, kill switch).

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

# Running Zetrax: routine, commands, problems, recovery

## Your 5-minute daily routine

1. Read the morning report (07:00 Lagos): money per book, closed trades, win rate, fees, green days, alerts.
2. Open `/dashboard`: P&L, open positions with their stops and targets, drawdown, green days, narratives, perps.
3. Read any 🚨 critical alert from the night and do what it says.
4. If real money is on: check that `/positions` matches your wallet or the Hyperliquid app.
5. Change nothing because of one trade or a feeling. Changes come from the reviews (every 10 and 25 trades) and go to
   paper first.

## Telegram commands

| Command | What it does |
|---|---|
| `/status` `/books` `/balance` | money and state of every book |
| `/positions` | every open position with entry, now, stop, targets (perps: leverage, liquidation) |
| `/lasttrade` `/ledger` | the last exit with every field; the last exits (Trade_Ledger.md) |
| `/narratives` | the CT narrative engine's scores right now |
| `/perps` | both paper perps books and the last hour's verdict per coin |
| `/reportdaily` `/report` | last 24 hours; everything since the start + a CSV file |
| `/risk` | the charter, the kill switch, stand-downs, cooldowns; `/risk clear`, `/risk reset BOOK` (6-digit code) |
| `/pause` `/resume` | stop / allow new entries in every book (exits keep running) |
| `/book NAME off\|on` | pause one book |
| `/panic` `/panic off` | stop every buy, sell real money, close real perps; undo |
| `/gaps` | data sources that failed in the last 24 h |
| `/backtest` `/backtest fast` `/backtest perps` | Trend Rider (with the new evidence bar), the fast lab, the perps rules |
| `/backtest basket` `/trend basket` | Trend Rider rules on JUP BONK WIF JTO PYTH RAY, each coin on its own history |
| `/trend add COIN` `/trend remove COIN` | let the paper book trend-basket trade a coin that passed (6-digit code) |
| `/live …` | Trend Rider real money (checklist, trial, arm, off, selftest, withdrawals) |
| `/perps live …` `/perps agent` `/perps setmaster 0x…` | real-money perps (separate switch) |
| `/setkey NAME VALUE` `/keys` | add an API key (the message is deleted at once); show keys masked |
| `/health` `/update` `/rollback` `/version` | bot health and code updates |

Keys you can add: `X_BEARER_TOKEN` (X/Twitter, pay-per-use), `TELEGRAM_ALERTS_CHAT_ID` (a separate alerts chat for
critical messages), plus the existing Helius, Jupiter, Birdeye, Nansen, fomoapi and GitHub keys.

## Real-money perps, step by step (when you decide to; it is off until then)

1. `/perps agent` shows the bot's agent address. In the Hyperliquid app: API → add an API wallet → paste that address.
   An agent can trade but can never withdraw.
2. `/perps setmaster 0xYOURWALLET` (your own Hyperliquid wallet; set once), then confirm with the code.
3. `/perps live selftest` on testnet (practice money): places an order far from the price and cancels it.
4. `/perps live trial`: at most $10 margin, 3x, every order waits for your tap. 7 days at most.
5. Mainnet needs the server console: `copybot perps-set network main` (you type MAINNET to confirm). Raising the
   guard's caps works the same way: `copybot perps-set max_leverage 10`, `max_order_usd`, `max_day_usd`.
   The bot itself can never change these.

## Troubleshooting

| You see | Do |
|---|---|
| "🔴 a bot part stopped" | `/health`; if it repeats, `/update` (a restart starts every part again) |
| `/narratives`: X feed off | `/setkey X_BEARER_TOKEN …` (developer.x.com, pay-per-use); without it nothing reaches A+ |
| many data gaps in `/gaps` | a source is down or rate-limiting; it recovers by itself, nothing trades on missing data |
| "global kill switch" | all books fell 6% from their peak: read `/reportdaily` and `/risk`, then `/risk clear` |
| a book "SHUTDOWN" | its drawdown switch tripped: review it, then `/risk reset BOOK` |
| "WALLET MISMATCH" / perps "mismatch" | real money is off; compare the wallet/app with `/positions`, then `/live clear` or `/perps live clear` |
| a sell keeps failing | it retries by itself; after 24 h without a route the position is written off |
| dashboard "DISCONNECTED" | open it again from `/dashboard` (the link carries the access token) |

## Incident recovery

1. **Stop new risk:** `/panic` (stops every buy, sells real money, closes real perps).
2. **Look:** `/health`, `/positions`, `/gaps`, the dashboard, and on the server `copybot logs`.
3. **Undo a bad update:** `/rollback` (or `copybot rollback` on the server). The watchdog also rolls back by itself
   when an update leaves the bot unhealthy.
4. **Restart parts:** `copybot restart` on the server; `copybot signer`, `copybot perps-guard` show the guards' logs.
5. **Lost server:** Solana wallet: `copybot wallet-export` (prints the key, type EXPORT) before anything else, import it
   into Phantom. Hyperliquid: your money is in YOUR wallet; revoke the agent in the Hyperliquid app (API page).
6. **Resume:** `/panic off`, then switch real money on again yourself only after the cause is understood.

## Rollback procedure

- Every `/update` first takes a code snapshot (the last 3 are kept), then installs, then checks the bot's health.
  Unhealthy within 90 s → automatic restore of the snapshot.
- `/rollback` restores the last snapshot by hand. The database, keys and the memory folder (Trade_Ledger.md,
  Learnings.md) are never touched by a rollback.

## Production checklist (before ANY real money)

- [ ] Your written go-ahead for the specific book, venue, limits and funding
- [ ] All tests passing (`for t in tests/test_*.py; do python3 $t; done`)
- [ ] Backtest validated out-of-sample: PF ≥ 1.3 with fees x3, Sharpe > 1.5 held-back, Monte Carlo survivable,
      neighbouring settings pass, walk-forward mostly green, 30+ trades
- [ ] Paper results in line with the backtest (2-4 weeks, perps 4 weeks, 30+ closed trades)
- [ ] Kill switch tested (the monthly `/panic` test)
- [ ] Alerts working, reconciliation clean for 7 days in a row
- [ ] Small size first; scale only if live results match paper

## The ten specialist roles (who checks what)

1. **Quant researcher**: strategy rules, backtests, the evidence bar (`strategy_v2.py`, `backtest_*.py`, `evidence.py`).
2. **Data engineer**: candles, feeds, raw store, gap log (`candles.py`, `feeds.py`).
3. **Narrative analyst**: the CT engine's weights, sybil filter, stages (`narrative.py`).
4. **Risk manager**: the charter and every limit (`risk.py`, `config.yaml risk:`).
5. **Execution engineer**: fills, quotes, the guards (`paper.py`, `live.py`, `perps_live.py`, `signer/`, `perpsguard/`).
6. **Security engineer**: keys, users, caps, sudoers, firewall (`system/`, `install.sh`).
7. **Reliability engineer**: watchdog, updates, rollback, threads (`update.sh`, `system/copybot-watchdog.*`, `ops.py`).
8. **Monitoring engineer**: Telegram, digests, dashboard, robot room (`notify.py`, `cmds.py`, `dashboard/`).
9. **Reviewer / auditor**: the ledger, reviews, Learnings.md, this checklist (`ledger.py`).
10. **Owner's liaison**: plain-words reports and the Trading_Context.md interview.

## Deployment

- Supported: systemd on the Vultr server (`install.sh` once, then `/update`). Auto-restart, root watchdog every 2 min.
- Docker (paper only, for a test machine): `docker compose up -d` with the files in `deploy/`. The real-money guards
  need their own users and stay a systemd feature.
- SSH allow-list: `copybot ssh-allow YOUR.IP` lets SSH in only from your IP and switches itself back after 10 minutes
  unless you run `copybot ssh-allow keep` from a working session (so you can't lock yourself out). The Vultr web
  console always works, even without SSH.

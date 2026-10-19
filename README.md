# Zetrax: Solana trading bot (paper first)

**Everything trades on paper (simulated money) until YOU switch real money on.** The bot runs seven books (copy,
consensus, Trend Rider, Trend basket, Narrative Rider, perps, perps-grok), each with its own simulated money. Real money
is built for all of them except perps-grok, through separate guard services that hold the wallet keys, and every switch
is **OFF** by default (`/real` shows them all). `execution.py` is an old locked stub that refuses to trade.

Install on a fresh Ubuntu server (as root), then send `/start` and `/update` to your Telegram bot:
`curl -fsSL https://github.com/energyisbackff-byte/copybot/archive/main.tar.gz | tar xz && cd copybot-main && bash install.sh`
(it asks for HELIUS_API_KEY, FOMOAPI_KEY and TELEGRAM_BOT_TOKEN). Guides: `docs/OPERATIONS.md`, `Trading_Context.md`.

**Trend Rider real money** (a slow daily SOL trend follower) works through its own signer service:
- `/live` shows the checklist: SAFETY items (signer, confirmed home address, a test withdrawal you saw arrive, a funded wallet, a passed self-test, no panic or mismatch) and STRATEGY items (v2 passed its backtest, 14 days on paper).
- `/live arm` switches it on when every item passes. `/live trial` is your capped override before the strategy is proven: the SAFETY items must still pass, positions are at most $10, and every buy waits for your tap.
- Either switch turns itself off after 7 days, and on any restart, `/panic`, wallet mismatch or drawdown switch. `/live off` turns it off. Exits are never blocked.
- The signer only signs SOL↔USDC swaps inside its wallet (buys at most $20 each and $100 a day) or transfers to your home address.

## Since 19 Oct 2026: the full build (everything on paper first)

- **Seven books with their own money:** copy, consensus, Trend Rider, **Trend basket** (the same Trend Rider rules on
  JUP/BONK/WIF/JTO/PYTH/RAY, each coin only after it passed its own backtest and you approved it), **Narrative Rider**
  (only A+ CT narratives), **perps** (long and short XRP/DOGE/LINK/UNI/ATOM/SOL on Hyperliquid prices, risk charter)
  and **perps-grok** (the 8x-19x spec as research). All paper. See `Trading_Context.md` for every rule and limit.
- **CT narrative engine:** X (with your `X_BEARER_TOKEN`), news, Dexscreener themes, GeckoTerminal trending and new pairs, scored
  0-100. Rule zero: no verified narrative, no trade.
- **Risk charter, ledger and reviews, alert tiers, morning report, robot room job panel.** Guides: `docs/`.
- **Real money is built for every book except the 8-19x research book, and every switch is OFF** until you turn it on:
  `/real` shows them all, `/real on BOOK` / `/real trial BOOK` / `/real off all`. Three wallets: the Trend Rider wallet,
  the coin wallet (Narrative Rider, Trend basket, copy, consensus) and your own Hyperliquid wallet for perps. Link your
  Phantom once with `/wallet link ADDRESS`: the guards can only ever pay back to it.

## Commands (run in this folder)
- Start: `./start.sh` (runs in background, logs to `bot.log`)
- Stop: `./stop.sh` (creates the `STOP` file; bot exits within ~30 s)
- Status: `./status.sh` (picked traders + why, cash/equity, open positions, last 10 actions)
- Re-pick traders now: `bash -lc 'python3 main.py select'`

## What it does
1. Once a day it pulls the FOMO 7d + 30d leaderboards (fomoapi, 500 credits/day) and reads each Solana-heavy trader's last 30 days of real swaps from Helius.
2. Keeps up to 3 traders who pass ALL checks: ≥10 closed trades, profit > 0, profit factor ≥1.2, win rate ≥30%, drawdown ≤50% of gross wins, top 3 wins ≤70% of all wins. Every pass or fail reason is saved and logged.
3. Every 25 s it checks those wallets for new swaps. When a trader buys, it simulates buying 0.25× their size (max 20% of equity) using a real Jupiter quote, then subtracts fees (2%), slippage (1%), a delay penalty (2%) and gas. When the trader sells, it sells the same fraction.
4. Safety: liquidity ≥ $50k, price impact ≤3%, max 6 positions, daily loss limit 15%, auto-shutdown at 40% drawdown, `STOP` file, duplicate checks by transaction signature.

## Telegram
Put `TELEGRAM_BOT_TOKEN` in your environment, open your bot in Telegram and send `/start`. The bot finds your chat automatically and sends selection changes, paper buys/sells, safeguards and a daily summary. Without a token it just writes to `bot.log`.

All settings are in `config.yaml`. Data is in `bot.db` (SQLite).

## Updating (server)
`copybot update` — downloads the latest code from GitHub, keeps your `.env`, `bot.db` and dashboard token, restarts the services.

### Making the GitHub repo private
`/update` downloads the code from GitHub. A private repo needs a read-only token, set like any other key. Do it in this order:
1. On GitHub: Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token.
   Repository access: **Only select repositories → copybot**. Permissions: **Contents: Read-only** (nothing else). Pick an expiry.
2. In Telegram: `/setkey GITHUB_TOKEN github_pat_...`. The bot deletes the message, restarts and reports "GitHub can read the repo".
3. Send `/update` once to check updates still work.
4. Only now: GitHub repo → Settings → General → Danger Zone → Change visibility → Private.

If the token expires or is wrong, `/update` says so and changes nothing. Set a new one with `/setkey GITHUB_TOKEN ...`.

First time (older installs without that command):
`curl -fsSL https://github.com/energyisbackff-byte/copybot/archive/main.tar.gz | tar xz && bash copybot-main/update.sh`

## Real-time paper execution (v2)
- Trader wallets are watched over a Helius WebSocket (`logsSubscribe`, confirmed commitment, works on the free plan). Polling remains as a safety net (every 120 s, or 25 s while the socket is down). Duplicates are removed by transaction signature.
- When a copy fires, the bot asks Jupiter for a real quote for our exact size, waits `exec_latency_s` (default 1.5 s, the time a real sign+send+land would take), re-quotes, and fills at that second quote. If there's no route or the price impact is above the limit, it's recorded as a failed fill, like it would fail live.
- The dashboard opens with the **bot room**: small robots that only move when that part of the bot really works (a coin walks through the same checks as the code; refused coins go to the bin with the exact reason; the CT desk sleeps until that feed exists). Buttons: `room` (show/hide), `▶ last` (replay the latest event), `motion` (off saves battery).
- Every fill records: trader block time, detection time, our fill time, latency, trader price vs our price (slippage in bps), price impact and fees. The dashboard and `/status` show median detection latency, average slippage vs the trader, and the fill-failure rate.
- Jupiter is used keyless (0.5 requests/s). Optional: add `JUPITER_API_KEY=...` to `/opt/copybot/.env` (free key at developers.jup.ag) for 1 request/s.

## Candidate sources (v3)
All candidates go through the same on-chain filters; sources only *suggest* wallets. Toggle in `config.yaml` → `sources:`.
| source | key | notes |
|---|---|---|
| fomo | FOMOAPI_KEY (have) | FOMO 24h/7d/30d/all leaderboards via fomoapi.io |
| dexscreener_buyers | none | Dexscreener official API (boosted/trending Solana tokens) → recent buyers read on-chain via Helius |
| birdeye | free `BIRDEYE_API_KEY` (bds.birdeye.so) | `/trader/gainers-losers` 1W/30d by realized PnL |
| nansen | `NANSEN_API_KEY` | free plan = 10 credits/day, off by default |
Add keys to `/opt/copybot/.env` and run `copybot restart`. Bot-like wallets (>20 closed trades/day) are rejected. Helius use is capped per selection run (`max_helius_credits_per_run`).

## Updating from Telegram
After running `copybot update` once on the server, send `/update` to the bot (only your chat; max once per 2 minutes). It replies "Updating…", pulls the latest code from GitHub, keeps your keys, data, dashboard token and settings, leaves real money off, restarts, and then says "Updated to commit …". `/version` shows the running commit. Update log: `/var/log/copybot-update.log`.
The bot user can run exactly one root command for this: `/usr/local/sbin/copybot-selfupdate` (no arguments), via `/etc/sudoers.d/copybot-update`.

## Adding API keys from Telegram
`/setkey BIRDEYE_API_KEY yourkey` → the bot deletes your message right away, saves the key in `/opt/copybot/.env`, restarts, and then confirms whether the key works. `/keys` shows which keys are set (only the last 4 characters).
Allowed names: BIRDEYE_API_KEY, JUPITER_API_KEY, NANSEN_API_KEY, HELIUS_API_KEY, FOMOAPI_KEY, GITHUB_TOKEN (read-only, only for private-repo updates). Nothing that enables live trading or touches wallets can be set this way. The bot user can only run `/usr/local/sbin/copybot-setkey`, which takes the key on stdin, never as a command-line argument. Note: the key still passes through Telegram's servers once; rotate it at the provider if you're worried.

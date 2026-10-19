# Zetrax architecture (six layers)

Every trading decision is plain code with fixed rules. No AI model is in the trading path, so a better AI model never
needs a rebuild of the trading engine (model-independent). Each layer has its own safety control.

```
 1 DATA        candles.py (Coinbase/Kraken daily + hourly)   chain.py (Helius, Jupiter)   wsmon.py (wallet stream)
               feeds.py (X API, news RSS, Dexscreener, GeckoTerminal, Hyperliquid) -> raw_feed (raw first) + data_gaps
                    │ control: raw data stored before use, every failure logged as a gap, nothing filled in silently
 2 SIGNAL      strategy_v2.py (Trend Rider)   narrative.py (CT narrative engine, 0-100 scores)   perps.signal()
               selection.py (trader picks)    sources.py
                    │ control: only closed candles; point-in-time data; every score logged (narrative_scores)
 3 DECISION    paper.Paper.on_buy (the gate, in order)   rider.py (Narrative Rider)   perps.PerpBook.open
               trend.py (Trend Rider runner)   basket.py (Trend Rider rules on approved extra coins)
                    │ control: deterministic rules; every refusal logged with the exact reason (b_signals)
 4 RISK        risk.py (charter, Kelly cap, fee edge, cooldowns, stand-down, kill switch, sweep, green days)
               veto.py (contract checks)   paper.py exits (volatility stop, 50% at TP1, breakeven, trailing)
                    │ control: nothing here can block a sell; limits checked before every order
 5 EXECUTION   paper: real Jupiter quotes / Hyperliquid order books with simulated latency and fees
               real money: live.py -> signer/ (Solana wallet guard)   perps_live.py -> perpsguard/ (Hyperliquid agent)
                    │ control: keys only inside the guards (separate users); caps in root-owned files; restarts
                    │ switch real money off; reconciliation every 60 s; mismatch = off
 6 MONITORING  notify.py (instant / critical / hourly digest)   cmds.py + main.py (Telegram)   ops.py (morning report,
               stall alarm, monthly checks)   ledger.py (Trade_Ledger.md, Learnings.md, reviews)   report.py
               dashboard/ (server.py read-only, index.html, room.js)   system/copybot-watchdog.* (root watchdog)
```

## Processes and threads

| Where | What | Writes |
|---|---|---|
| `copybot.service` main loop | trader selection, wallet polling, copy/consensus exits every 30 s, Trend Rider + basket, `/live`, ops.tick | copy, consensus, trend, trend-basket, trend-live |
| thread `narrative` | the narrative engine every 5 min | mentions, narratives, narrative_scores, raw_feed |
| thread `narrative-book` | Narrative Rider buys from the A+ queue, exits every 30 s | narrative |
| thread `perps` | Hyperliquid data every 30 s, signals once an hour, both paper perps books, real-money perps | perps, perps-grok, perps-live |
| thread `CommandListener` | Telegram commands and approval taps | kv only |
| `copybot-dashboard.service` | read-only web dashboard | nothing |
| `copybot-signer.service` | Solana wallet key, SOL↔USDC swaps and transfers home only | its own state |
| `copybot-perpsguard.service` | Hyperliquid agent key (trade only, never withdraw), caps | its own state |
| `copybot-watchdog.timer` | root watchdog every 2 min, rollback on a bad update | /var/lib/copybot |

The database is SQLite in WAL mode; each thread has its own connection and keeps write transactions short (network
calls never happen inside a write).

## Failure handling

- A source fails → a data gap is logged, the engine continues with what it has; no narrative = no trade.
- A thread stops writing its heartbeat → one critical alert, one "recovered" alert.
- A sell fails → it retries with backoff; after 24 h without a route the position is written off at $0.
- A real-money mismatch, a restart, `/panic`, 7 days → real money off.
- A bad `/update` → automatic rollback to the last snapshot.

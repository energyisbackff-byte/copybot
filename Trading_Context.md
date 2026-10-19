# Zetrax Trading Context

The bot's long-term operating context: what it trades, how much it may risk, when it must not trade, and what it
needs before real money. Written 19 Oct 2026 from the owner's two documents (the Master Instruction and the Master
Build Prompt) and his instructions in chat ("build everything", "run with paper first", "I'm not funding the wallet
now"). Where the two documents disagreed, the **stricter** rule was taken. Lines marked **(default, change me)** were
chosen because the owner asked for everything to be built at once instead of the one-question interview; each one
can be changed by telling the bot's engineer, and the interview questions are listed at the end.

## 1. Owner

- Beginner: plain words, phone first (Telegram + the dashboard), never needs the server console for daily use.
- Location: Lagos (UTC+1). The bot works in UTC; the morning report arrives 06:00 UTC = 07:00 Lagos.
- Real money: **none yet**. Every book runs on paper (simulated money, real prices and quotes). The real-money parts are
  built and **switched off**.

## 2. The rule over everything

**NO VERIFIED NARRATIVE, NO TRADE. NO MEASURABLE EDGE, NO TRADE. NO PROTECTED EXIT, NO TRADE.**
Flat is a position. Zero-trade days are normal. No book is promised a daily profit; the target is a statistically
defensible edge after all costs, with most traded days green and losses kept small.

## 3. Books (each has its own money; one can never hide another's losses)

| Book | What it trades | Paper money | Status |
|---|---|---|---|
| copy | copies the selected Solana traders (memecoins, long) | $52 | paper, being re-proven (new exits) |
| consensus | 2+ selected traders buying the same coin within 15 min | $25 | paper, being re-proven |
| trend (Trend Rider v2) | SOL/USDC daily trend (rules untouched since 14 Oct 2026) | $30 | paper; the only book with a real-money path today (`/live`) |
| trend-basket (Trend basket) | the Trend Rider v2 rules on JUP, BONK, WIF, JTO, PYTH, RAY: a coin only after it passed its own backtest (`/backtest basket`) and you approved it (`/trend add COIN`) | $60 **(default, change me)** | paper only (the wallet guard swaps only SOL/USDC) |
| narrative (Narrative Rider) | long spot Solana coins, only from an A+ CT narrative | $50 **(default, change me)** | paper only |
| perps | long AND short perps: XRP, DOGE, LINK, UNI, ATOM, SOL (Hyperliquid prices) on the risk charter | $100 **(default, change me)** | paper; real-money path built, off (`/perps live`) |
| perps-grok | the screenshot's 8x-19x spec, up to half the book on an A+ signal | $100 **(default, change me)** | research, paper only |

## 4. Markets and timeframes

- Solana spot through Jupiter (copy, consensus, narrative, Trend Rider).
- Perps on Hyperliquid (Jupiter Perps and Drift/Velocity do not list XRP, DOGE, LINK, UNI or ATOM; checked 18 Oct 2026).
- Timeframes: Trend Rider = daily candles (decides after 00:05 UTC); perps = closed 1-hour candles; narrative engine =
  every 5 minutes; copy/consensus = as the traders trade; every open position is checked every 30 seconds.

## 5. Entry rules

- **Trend Rider:** close above SMA50, SMA20 above SMA50, close above the previous 20 days' highest close; entry right
  after the daily close, skipped if more than 6 h late or 5% past the signal.
- **Copy / consensus:** every buy passes the risk gate: rules (panic, pause, limits, kill switch, stand-down, cooldown),
  storm pause, liquidity ≥ $50k, contract checks (no mint or freeze authority, top-10 holders ≤ 30%, Token-2022 checks),
  real Jupiter quote with price impact ≤ 3%, buy-then-sell test ≤ 6% loss, the exit plan, the fee edge, latency.
- **Narrative Rider:** only an A+ candidate (score ≥ 85 and every gate): a narrative that is emerging or accelerating,
  confirmed by at least 2 independent source families of which one is X or news (market lists alone are not a
  narrative), not fake-account driven, with a coin showing real 1h volume growth and price confirmation, liquidity ≥
  $50k and a pair at least 1 h old. Then the same full buy gate as copy.
- **Perps:** decide only on a CLOSED 1h candle: trend regime (EMA20/EMA50 at least 0.4% apart), not abnormal
  volatility (ATR under 3x normal), a breakout of the previous 20 closes in the trend's direction (green candle for a
  long, red for a short), funding not crowded against us, and a CT/news narrative for that coin right now (rule zero).

## 6. Exit rules (all set BEFORE the entry; targets are intended prices, a real fill can differ)

- Copy / consensus / narrative: stop = 1.5x ATR of 15-minute candles (between 6% and 25%); sell 50% at the first
  target (1x the stop distance), move the stop to breakeven, trail the rest by 1x the stop distance; final target 3x
  the stop distance (never moved further away); 24 h time limit. Narrative Rider adds the time-stop: no first target
  in 4 h AND the narrative decays → sell.
- Perps: stop = 1.5x ATR14 from the close; TP1 = 1R (close 50%, stop to breakeven); TP2 = 2R; opposite signal or 24 h
  → close. Liquidation must sit at least 1.5x the stop distance away.
- Trend Rider: stop 3x ATR14 below, trailing up; exit on a daily close below SMA50.
- **Sells are never blocked by any rule.**

## 7. Risk charter (applies to every new book; Trend Rider keeps its tested 1%)

| Rule | Value | Source |
|---|---|---|
| Risk per trade | **0.25%** of the book (fees included); copy/consensus 1% while being re-proven | Master Instruction (stricter) |
| Kelly | after 30 closed trades, 0.25x Kelly may only LOWER the risk | Build Prompt |
| Ceiling | 1% per trade for any book | Build Prompt |
| Daily loss | **-1%** of a new book stops its entries for the UTC day | Master Instruction (stricter) |
| Max drawdown | **-5%** from a new book's peak shuts it (`/risk reset BOOK` after a review) | Master Instruction (stricter) |
| Global kill switch | all books together -6% from their peak → no new entries anywhere (`/risk clear`) | Build Prompt |
| Fee edge | the first target must pay 3x the round-trip costs | Build Prompt |
| Stand-down | 2 losing stop-outs in a day → that book rests until 00:00 UTC | Build Prompt |
| Cooldowns | a stopped coin is blocked 24 h; a narrative with 2 losing trades is blocked until it accelerates again | Build Prompt |
| Profit sweep | 00:05 UTC: profit above each book's baseline is set aside, never traded again | Build Prompt |
| Correlation | coins moving together in the same direction count as one bet (max 2) | Master Instruction |
| perps-grok | research: daily -25%, drawdown -60%, left out of the global kill switch | the screenshot spec, paper only |

## 8. When trading is prohibited

`/panic` · `/pause` · a book's own pause (`/book NAME off`) · the global kill switch · a book's drawdown switch · its
daily loss limit · its stand-down · a coin's cooldown · a blocked narrative · the storm pause (SOL -5% within an hour) ·
the bot more than 6 h late for Trend Rider's daily close · no narrative (new books) · no edge after costs.

## 9. Notifications

- Instant (lock-screen): fills, sells, stops, targets, errors, approval requests, reviews, the morning report.
- Critical (marked 🚨, also sent to the alerts chat when `TELEGRAM_ALERTS_CHAT_ID` is set): kill switches, panic,
  liquidations, mismatches, stalled bot parts.
- Hourly digest: routine refusals and the profit sweep, grouped into one message.

## 10. Autonomy

- The bot may trade every paper book on its own, inside the charter.
- It may never change a strategy by itself. Reviews (every 10 trades; deep every 25, with Learnings.md) only suggest
  changes, and a change goes to paper first.
- Real money: only after the owner switches it on, with a tap on every order in trial mode and on the first 10 orders
  in arm mode. Every restart switches real money off. 7 days at most per switch-on.

## 11. Go-live thresholds (the production checklist; nothing here can be skipped by a command)

- Evidence: backtest out-of-sample PF ≥ 1.3 with fees x3, Sharpe > 1.5 on held-back data, Monte Carlo 95% drawdown
  survivable, neighbouring settings also pass, walk-forward windows mostly green, at least 30 trades.
- Paper: 2-4 weeks (perps 4 weeks) and 30+ closed paper trades, results in line with the backtest.
- Safety: the guard running, the home/master address set, a test withdrawal (Solana) or the testnet selftest
  (Hyperliquid) passed, reconciliation clean, `/panic` tested.
- The owner's explicit go-ahead, the funding he chooses, small size first.

## 12. Interview questions still open (answer any time; the defaults above stay until then)

1. How much real money would you ever put in each book, at most?
2. Which books matter most to you: Trend Rider, copy, Narrative Rider, or perps?
3. Keep the strict charter (0.25% per trade, -1% day, -5% drawdown) or the wider one (0.5-1%, -3%, -6%)?
4. Should perps-grok (8x-19x) ever trade real money, or stay research?
5. Do you want the paid X/Twitter feed (about $1.50 a day at the default budget of 300 posts)?
6. Which phone alerts should never be silenced, and which can wait for the hourly digest?
7. After real money starts: how many losing days in a row before everything pauses for your review?

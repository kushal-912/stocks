# QuantDesk: the complete guide

This guide explains everything in QuantDesk:

- **Part A:** the big picture.
- **Part B:** every page of the website, top to bottom: each number, column, badge and button.
- **Part C:** every algorithm the agent uses, with formulas and worked examples based on the real settings.
- **Part D:** a glossary.

All numbers quoted are the current defaults in `config/settings.yaml`. If you change a setting, the behaviour described here changes with it. The setting names are given in `code` so you can find them.

---

## Contents

- **Part A: The big picture**
  - A1. What QuantDesk is
  - A2. A day in the life of the agent
  - A3. Paper vs live: why they are the same
  - A4. Where everything is stored
- **Part B: The website, page by page**
  - B1. Things on every page (sidebar, top bar)
  - B2. Overview
  - B3. Portfolio
  - B4. Research
  - B5. Stock page
  - B6. Activity
  - B7. Evaluation
  - B8. Backtest
  - B9. Settings
  - B10. Status badges: what every status word means
- **Part C: The algorithms in detail**
  - C1. The universe: which stocks are considered
  - C2. Price data and why it is "split-adjusted only"
  - C3. Stage 1: the whole-market screener
  - C4. Market regime and breadth
  - C5. Stage 2: deep research on the shortlist
  - C6. The six model votes, one by one
  - C7. Forecasting models (ARIMA, ETS, Kalman, GARCH)
  - C8. Machine learning (walk-forward gradient boosting)
  - C9. News and sentiment (lexicon + Claude)
  - C10. Statistical diagnostics and regimes
  - C11. Final conviction score and vetoes
  - C12. Stage 3: Claude's review
  - C13. The portfolio policy: exits, entries, swaps, sizing
  - C14. Stops: initial and trailing
  - C15. Execution: the open, the ticks, the close
  - C16. The paper exchange: how fills are simulated
  - C17. Charges and taxes (with a worked example)
  - C18. The risk guard: checks on every single order
  - C19. Accounting: the virtual sub-account
  - C20. The evaluation maths
  - C21. The backtest engine and how it avoids the future
  - C22. The tamper-evident ledger
  - C23. The autopilot scheduler and crash recovery
- **Part D: Glossary**

---

# Part A: The big picture

## A1. What QuantDesk is

QuantDesk is an **autonomous trading agent** for Indian stocks listed on the NSE. Nobody clicks "buy" or "sell". Every trading day it does five things on its own:

1. Looks at every stock on the exchange.
2. Narrows them down to the most promising few.
3. Studies those few in depth with several independent models.
4. Decides what to own and how much.
5. Places the orders and protects every position with a stop-loss.

It holds positions for days to weeks (swing trading), buys only for delivery (CNC), and never short-sells.

The website is a window into what the agent is doing and why. Apart from the kill switch and the backtest, you can't change anything from it, and that is deliberate: the evaluation must be the agent's own work.

## A2. A day in the life of the agent

All times are IST, on trading days only.

| Time | Phase | What happens |
|---|---|---|
| **08:15** | Research & plan (`premarket`) | Downloads yesterday's prices for ~2,300 stocks and screens them. Researches the top 25 deeply, asks Claude to review (if configured), and writes today's plan. |
| **09:15** | First tick | Confirms the market is really open (detects holidays). Places a stop-loss order for every holding. |
| **09:20** | Execute at open (`open`) | Sells first, then buys with limit orders. Skips stocks that jumped too much at the open. |
| **every 60 s** | Tick | Checks for fills, records new positions, keeps stop-losses in place, and saves a value snapshot every 5 minutes. |
| **15:10** | Cancel unfilled (`cancel`) | Cancels buy orders that never filled. |
| **15:45** | Close & snapshot (`eod`) | Expires day orders, applies corporate actions, raises trailing stops, records the day's value next to NIFTY's, and writes the journal. |

Times are set in `agent.schedule`.

## A3. Paper vs live: why they are the same

The agent is one piece of code (`quantdesk/trader/agent.py`). It is handed a **broker** object and never asks which one it has:

```
TradingAgent → OrderExecutor → RiskGuard → Broker
                                           ├─ PaperBroker   (now: simulated exchange, real prices)
                                           ├─ GrowwBroker   (later)
                                           └─ ZerodhaBroker
```

Everything that decides what to trade is identical in both modes: the screener, research, policy, risk checks and schedule.

The only differences are:
- **Order matching:** who matches the orders (the simulator vs the real exchange).
- **Price source:** paper uses Yahoo 1-minute bars, live uses the broker's own feed.

## A4. Where everything is stored

| Path | What's in it |
|---|---|
| `data/quantdesk.db` | **The ledger** (SQLite): every event, decision, order, fill, position, trade, daily value, plus the paper account itself |
| `data/market/*.parquet` | The price panel: 4 years of daily open/high/low/close/volume for every NSE stock |
| `data/universe.csv` | The list of NSE stocks (refreshed weekly) |
| `data/sectors_extra.json` | Sector labels looked up for stocks NSE doesn't classify |
| `data/backtest/latest.json` | The most recent backtest result |
| `data/KILL` | Exists only while the kill switch is on |
| `data/autopilot.lock` | Prevents two agents running at once |
| `config/settings.yaml` (+ `settings.local.yaml`) | All the parameters |

---

# Part B: The website, page by page

Open it at **http://localhost:8000** while `qd start` is running. Pages refresh themselves every 15 to 60 seconds; you never need to reload.

## B1. Things on every page

### Left sidebar

- **QuantDesk logo:** decoration.
- **Navigation:** Overview, Portfolio, Research, Activity, Evaluation, Backtest, Settings. Stock pages highlight "Research".
- **Footer:**
  - **"Evaluation since <date>":** the agent's **inception date**, the day the paper account was first used.
  - **"Start capital ₹…":** the money it began with (₹50,000).

### Top bar (left to right)

| Element | Meaning |
|---|---|
| **PAPER / LIVE · groww** | The trading mode. PAPER (blue) means simulated money; LIVE (red) means real orders through a broker. Set by `execution.mode`. |
| **● Agent running / Agent offline** | Green while the autopilot sends a heartbeat (at least every 60 s). Red if none has arrived for 3 minutes, which means the agent is not running. The tooltip shows when it last checked in. |
| **● Market open / closed · HH:MM IST** | Whether NSE is in its 09:15–15:30 session on a weekday, plus the current IST time. |
| **Search box** | Type any NSE symbol or company name and press Enter, or click a result, to open that stock's page. |
| **Moon icon** | Switches between light and dark theme. The choice is remembered. |
| **Kill switch** | Puts the agent in **close-only mode**: no new buys, but exits and stop-losses keep working. It asks for confirmation and turns solid red while on. Click again to turn it off. The same switch is `qd kill on/off` in the terminal. |

## B2. Overview

### Header

- **Title line:** "Autonomous paper trading · started <date> with ₹50,000".
- **Phase timeline (right):** four bars, one per phase: *Research & plan*, *Execute at open*, *Cancel unfilled*, *Close & snapshot*.
  - Under each phase: either "done HH:MM", the current status, or "at HH:MM" if not reached yet.
  - Bar colour: green = done, blue = running, red = failed, grey = skipped (holidays, or "too late in the session").

A yellow box appears if the agent has never started.

### KPI cards (top row)

| Card | Big number | Small line under it |
|---|---|---|
| **Equity** | Cash + current value of the agent's positions | Total return since inception |
| **vs NIFTY 50** | Excess return: how much more (or less) the agent's money is worth than the same money in NIFTY 50, i.e. agent equity ÷ NIFTY-equivalent − 1 | NIFTY's return over that period |
| **Today** | Change in equity since yesterday's close (or since inception on day one) | The same as a percentage |
| **Max drawdown** | The worst fall from a previous peak, since inception | NIFTY's worst fall over the same period |
| **Invested** | Share of equity currently in stocks | Cash available |
| **Positions** | Number of stocks held | Realised P&L: profit or loss already locked in by closed trades |

Green text means positive, red means negative.

### Equity vs NIFTY 50 (chart)

- **Blue line:** the agent's equity, one point per trading day.
- **Orange line:** what the same starting money would be worth in NIFTY 50.
- **Hovering:** shows both values for that day.
- **Empty chart** until the first trading day closes.

### Scorecard

The four evaluation checks explained in B7, in short form. "details →" opens Evaluation.

### Holdings

One row per stock the agent owns. Click a row to open the stock page.

| Column | Meaning |
|---|---|
| Stock | NSE symbol |
| Qty | Shares held |
| Avg | Average buy price |
| LTP | Last traded price (latest snapshot) |
| P&L | Unrealised profit/loss in ₹ and % (before the charges of selling) |
| Stop | The protective stop-loss price. If the price trades at or below it, the position is sold automatically. |
| Value | Qty × LTP |

### Agent activity

The 14 latest entries from the ledger, newest first, with time (today) or date (earlier). The small grey word is the event kind; all kinds are listed in B6. Warnings and errors are red.

### Latest decisions

The agent's most recent BUY/SELL decisions.

| Column | Meaning |
|---|---|
| Day | The day the plan was made |
| Action | BUY (blue) or SELL (yellow) |
| Stock, Qty | What and how many |
| Score | The final conviction score, −1 to +1 (C11) |
| Why | The agent's own explanation: rank, momentum, model votes, ML, news, or the exit rule that fired |
| Status | planned / submitted / skipped / failed / void (B10) |

## B3. Portfolio

### KPI cards

| Card | Meaning |
|---|---|
| **Market value** | Total value of all positions; "cash" under it |
| **Unrealised P&L** | Sum of open positions' profit/loss |
| **Open risk to stops** | How much would be lost if **every** stop were hit right now: Σ (LTP − stop) × qty for positions above their stop. Also shown as % of equity. Gaps can make real losses bigger. |
| **Closed trades** | Number of completed round trips, and the % that made money |
| **Realised P&L** | Total profit/loss of closed trades **after all charges**; total charges paid under it |

### Tab "Open positions"

Click a row to open the stock page.

| Column | Meaning |
|---|---|
| Stock | Symbol and sector |
| Since | Purchase date |
| Qty / Avg cost / LTP | Shares, buy price, current price |
| P&L | ₹ and % since purchase |
| Stop-loss | Current stop price, and how far below the current price it is |
| Value | Position value and its % of equity |
| Entry thesis | The reason recorded when it was bought |

### Tab "Closed trades"

| Column | Meaning |
|---|---|
| Held | Entry date → exit date, and holding days (trading days) |
| Entry / Exit | Buy and sell prices |
| P&L (net) | Profit/loss **after** buy and sell charges, in ₹ and % of the amount invested |
| Charges | Total charges of the round trip |
| Why it exited | The rule that sold it (C13/C14): "stop-loss hit", "trailing stop (profit locked)", "conviction decayed…", "trend broken…", "replaced by …", "time stop…" |

### Tab "Allocation"

One bar per sector showing its ₹ value and share of equity, plus a grey bar for cash.

## B4. Research

This page shows the agent's morning homework.

### Header

- **"Prices as of <date>":** the last completed trading day used.
- **"researched HH:MM":** when the research ran.
- **"Claude review: …":** what happened in stage 3:
  - "N rejected of M"
  - "skipped (no LLM credentials…)": no API key
  - "disabled"
- **Date dropdown (right):** view any earlier day's research.

### Today's funnel

Seven boxes; each number is how many stocks survived that step.

| Box | Meaning |
|---|---|
| **NSE equities** | Every EQ-series stock on NSE (~2,333) |
| **Liquid** | Survived the liquidity rules **and** is in today's top 750 by trading value (C1, C3) |
| **Uptrend** | Liquid and above its 200-day average, with the 50-day average above the 200-day |
| **Eligible** | Uptrend, not overextended, not too volatile |
| **Deep research** | Stocks that got the full multi-model analysis: the top 25 eligible plus every current holding |
| **Candidates** | Researched shortlist stocks that were not vetoed (bad news or a Claude rejection) |
| **Plan** | Orders planned for the open |

### Market regime card

| Field | Meaning |
|---|---|
| Badge **risk-on / risk-off** | risk-off when NIFTY is below its 200-day average **and** its 50-day average is below the 200-day (a confirmed downtrend). The regime filter is **off** by default (`agent.regime_filter: false`), so risk-off is informational. When on, risk-off caps investment at 40%. |
| NIFTY 50 | Index level, and % above or below its 200-day average |
| 50 / 200-day avg | The two moving averages |
| Breadth | % of liquid stocks above their own 200-day average. Above 50% is a healthy market; well below is weak. |
| Equity / cash at plan time | What the agent had to work with |

### Plan for the open

Each planned order:
- BUY/SELL badge, symbol (a link), quantity × reference price (yesterday's close), the stop it will use, and the score.
- The full reason underneath.

### Tabs "Deep research" / "Top 150 screened"

"Deep research" lists only researched stocks; "Top 150 screened" lists the 150 best-ranked liquid stocks. Click any row for its stock page. Column headers sort.

| Column | Meaning |
|---|---|
| # | Rank by screen score among liquid stocks (1 = best) |
| Stock | Symbol, company name, sector |
| Screen | Stage-1 score, −1 to +1. It is a percentile: +0.98 means better than 99% of liquid stocks (C3). |
| Deep | Stage-2 composite of the model votes, −1 to +1 (C6) |
| Final | 0.4 × Screen + 0.6 × Deep, with a meter. ≥ +0.30 is needed to buy (C11). |
| 12-1m mom | Return from 12 months ago to 1 month ago |
| of 52w high | Price as % of its highest price in the past year |
| Volatility | Annualised volatility of daily returns over ~3 months |
| ₹ Cr/day | Median daily traded value over 20 days, in ₹ crore |
| Verdict | "approve"/"reject" from Claude, a red veto (e.g. strongly negative news), "eligible", or "filtered" (failed a stage-1 gate) |

## B5. Stock page (any symbol)

Reached by clicking a stock anywhere, or through search.

### Header

- **Symbol:** with a "held" badge if the agent owns it.
- **Company name · sector.**
- **Price:** last close, with change and date.
- **Run fresh research:** runs the full deep analysis right now on any stock, including ones the agent never looked at. It takes 10–30 seconds and is cached for 30 minutes. It uses Yahoo's standard history for that one stock, so numbers can differ slightly from the morning research.

### Price chart

- **Candles:** green = closed higher than it opened, red = closed lower. About two years are shown; drag to pan.
- **Yellow line:** 50-day moving average.
- **Purple line:** 200-day moving average. A stock above both, with the 50 above the 200, is in an uptrend.
- **Arrows:** blue ▲ BUY and orange ▼ SELL mark the agent's actual fills, with quantity.
- **Dashed lines (if held):** red **stop** and grey **entry** price.
- **Legend:** shows date, Open, High, Low, Close for the bar under the cursor.

### Model votes

Six horizontal bars, one per model, each from −1 (bearish, red, left) to +1 (bullish, blue, right). They are explained in C6:

- trend
- momentum
- mean reversion
- forecast
- ml
- sentiment

"final ±x.xx" at the top right is the final conviction score. The date shows which research the votes come from.

### Forecast & volatility

| Field | Meaning |
|---|---|
| 5-day expected | Ensemble forecast of the 5-day return, ± one standard deviation |
| P(up), ensemble | Probability the 5-day return is positive, assuming a normal distribution: Φ(expected/std) |
| ARIMA / ETS / Kalman | Each model's own 5-day forecast and its t-stat (forecast ÷ uncertainty). Below ±1 is weak, and that is normal. |
| Volatility now / fcst | GARCH current annualised volatility and its 5-day-ahead forecast |
| Regime | Market state from the 3-cluster model: bull, sideways, bear/quiet, bear/stress (C10) |
| Character | trending / mean-reverting / random-walk-like, from the Hurst exponent |

### Machine learning

| Field | Meaning |
|---|---|
| P(up in 5d) | The model's probability that the stock is higher in 5 trading days |
| Out-of-sample accuracy | How often it was right on data it had never seen, compared with the **naive** rate of always guessing the most common outcome |
| AUC | Ranking skill: 0.50 = coin flip, 0.55 = useful, 0.60+ = rare for daily stock data |
| Predictions tested | How many out-of-sample predictions the accuracy is based on |
| Top features | The inputs the model relied on most |

Notes such as "ML failed: not enough history" appear for stocks with under ~2.5 years of data. "ML has no out-of-sample skill… vote zeroed" means AUC ≤ 0.50, so the ML vote counts as zero.

### News

- **Header:** aggregate sentiment and the method (`llm` = Claude, `lexicon` = word list, `mixed` = both).
- **Headlines:** each with its sentiment (−1 to +1), source, date, and Claude's one-line rationale when available. The links open the article.

### Agent's history with this stock

- **"Holding…" box:** if held, the quantity, entry date and price, and current stop.
- **Claude verdict:** if reviewed, the approve/reject with reasoning.
- **Rationale:** the agent's text from the latest research.
- **Past trades:** every closed trade in this stock, with dates, P&L and exit reason.

## B6. Activity

### "Verify integrity" button

Recomputes the hash chain of the whole event log (C22):
- **Green "Ledger intact · N events":** nothing was edited.
- **Red "Tampered at #id":** the first altered entry.

### Tab "Event log"

The dropdown filters by event kind.

| Column | Meaning |
|---|---|
| Time | Date and time to the second |
| Event | The kind (badge colour: red = error, yellow = warning) |
| Detail | Human-readable message |
| Hash | First 10 characters of the entry's fingerprint; hover for the full value |

The event kinds:

| Kind | When it's written |
|---|---|
| `inception` | The first time the agent runs on an account |
| `autopilot_start` / `autopilot_stop` | Scheduler started or stopped |
| `premarket` | Morning research finished (with funnel counts and plan summary) |
| `open` | Open phase finished (exits, entries, skips) |
| `order` | Any order sent: purpose (ENTRY/EXIT/STOP), side, qty, type, price, and result |
| `fill` | An order executed, with price |
| `position_opened` / `position_closed` | A position was created or ended (closed shows P&L, days, reason) |
| `skip` | A planned buy was skipped (gap-up, no cash, no price) |
| `cancel` | Unfilled entries cancelled at 15:10 |
| `eod` | Day closed: equity, return since inception, stops raised |
| `holiday` | No index prints by 09:45, so the day is treated as a holiday |
| `corporate_action` | Quantity changed because of a split or bonus; prices rescaled |
| `kill_switch` | Turned on or off from the web app |
| `phase_failed`, `tick_failed`, `sync_failed`, `cancel_failed`, `autopilot_error` | Something went wrong; the details include the error. A phase is retried up to 3 times a day; ticks retry every minute. |
| `sectors`, `paper_reset` | Sector lookup problems; paper account reset (`qd paper reset`) |

### Tab "Orders"

Every order the agent sent.

| Column | Meaning |
|---|---|
| Time | When sent |
| Purpose | **entry** (opening buy), **exit** (planned sell), **stop** (protective stop-loss) |
| Side | BUY / SELL |
| Qty / Type | Shares; MARKET, LIMIT or SL-M |
| Limit / trigger | Limit price, "trg" + trigger for stops, or MKT |
| Filled at | Average execution price |
| Status | OPEN, COMPLETE, CANCELLED, EXPIRED, REJECTED, BLOCKED (B10) |
| Reason | Why it was sent, plus the broker's or guard's message if it didn't complete |

## B7. Evaluation

This is the report card for the 6-month test. The yardstick is **NIFTY 50 bought with the same money on the same day**.

### Verdict so far

| Check | Pass | Warn | Fail |
|---|---|---|---|
| **Beating NIFTY 50** | excess return > 0 | – | ≤ 0 |
| **Drawdown control** | max drawdown better than −15% | −15% to −25% | worse than −25% |
| **Risk-adjusted return** (needs ≥ 20 days) | Sharpe > 1 | 0.5–1 | < 0.5 |
| **Statistical evidence** | alpha t-stat > 2 | after 120 days and t ≤ 2 | – |

"pending" means not enough data yet.

**How to read this** (the box on the right) explains why excess return and drawdown matter more than raw profit. All figures are after charges and before income tax.

### KPI cards

| Card | Formula / meaning (C20) |
|---|---|
| **Return** | Equity ÷ starting capital − 1; NIFTY's return under it |
| **Excess return** | (1 + agent return) ÷ (1 + NIFTY return) − 1, with the **alpha t-stat** under it |
| **Sharpe** | Annualised excess return over the risk-free rate (6.5%) per unit of volatility; **Sortino** (counts only downside volatility) under it |
| **Volatility** | Annualised standard deviation of daily returns; **beta** (sensitivity to NIFTY) under it |
| **Max drawdown** | Worst peak-to-trough fall; NIFTY's under it |
| **Avg invested** | Average share of equity held in stocks |

### Charts

- **Growth of capital:** agent (blue) vs NIFTY (orange), in ₹.
- **Drawdown from peak:** how far below its previous high each line was on each day. 0% means at a new high.

### Monthly returns

One row per month: agent %, NIFTY %, and the difference (bold green when the agent won that month).

### Trading statistics

| Field | Meaning |
|---|---|
| Closed trades | Completed round trips |
| Win rate | % with positive P&L after charges. Momentum systems often win under 50% of the time but win bigger than they lose. |
| Average win / loss | ₹ and % per winning and losing trade |
| Profit factor | Total ₹ won ÷ total ₹ lost. Above 1 is profitable; 1.5+ is good. |
| Expectancy per trade | Average P&L per trade |
| Average holding | Trading days per trade |
| Charges paid | All brokerage, taxes and fees |
| **P&L by exit reason** | Total P&L and count for each exit rule. Shows which rules make or lose money. |

## B8. Backtest

This replays the agent's rules on past prices.

### Controls

- **From / To:** the period. Leave "To" empty for "until today".
- **Capital ₹:** starting money. **Minimum ₹23,000** (C21 explains why); smaller amounts are rejected with an explanation.
- **Run backtest / Stop:** start, or cancel a running replay.
- **Progress bar:** % done, the date being simulated, and equity at that date.
- **"How this avoids knowing the future…" (expandable):** the no-look-ahead rules and what is not replayed.

### KPI cards (update live)

| Card | Meaning |
|---|---|
| **Equity · <date>** (Final value when done) | Simulated account value; return under it |
| **NIFTY 50, same money** | NIFTY bought with the same capital on the start date |
| **Agent vs NIFTY** | Difference in returns; after the run, also Sharpe and CAGR (yearly growth rate) |
| **Max drawdown** | Worst fall so far (NIFTY's after the run) |
| **Closed trades** | Count, % that made money, % that beat NIFTY over their own holding days |
| **Realised P&L** | Sum of closed trades after charges; open positions and cash under it |

### Live chart

Agent vs NIFTY in ₹, growing as days are simulated.

### Trade log (live)

A streaming console; tick "follow" to auto-scroll.

| Tag | Line contents |
|---|---|
| **START** | Capital, number of days, universe rule, charges model |
| **BUY** (blue) | Date, qty, symbol, fill price (next day's open + slippage), value, stop price, the reason (rank, momentum, % of 52-week high) |
| **SELL** (green = profit, red = loss) | Date, qty, symbol, sell price, buy price and date, **P&L ₹ and %**, **NIFTY's move over the same days**, days held, and exit reason |
| **SKIP** | A planned buy not executed: it opened more than 3% above yesterday's close, or there wasn't enough cash at the open |
| **NOTE** | Why nothing was bought on idle days (e.g. "portfolio full", "position too small…"), shown at most every 20 days per reason |

### "Every trade" table

Tabs show All, Winners or Losers; headers sort.

| Column | Meaning |
|---|---|
| Bought / Sold | Date @ price |
| Qty / Days | Shares; trading days held |
| P&L (after charges) | ₹ |
| Return | % on the amount invested |
| NIFTY same days | What NIFTY did between the same entry and exit dates |
| vs NIFTY | Return minus NIFTY's return: did this trade beat simply holding the index? |
| Why it sold | Exit rule |

**"Still open at the end"** lists positions held when the replay ended, valued at the last close (unrealised P&L).

## B9. Settings

This page is read-only. To change values, edit `config/settings.local.yaml` and restart `qd start`.

### Agent policy

| Label | Setting | Default | Meaning |
|---|---|---|---|
| Starting capital | `agent.initial_capital` | ₹50,000 | Paper account cash at inception |
| Max positions | `agent.max_positions` | 6 | Most stocks held at once |
| Min position size | `agent.min_position_value` | ₹5,000 | Smaller buys are skipped; fixed charges would eat them |
| Max per sector | `agent.max_per_sector` | 2 | Diversification rule |
| Stocks deep-researched daily | `agent.shortlist_size` | 25 | Size of the stage-2 shortlist |
| Min conviction to buy | `agent.min_final_score` | 0.30 | Final score needed to open a position |
| Sell below conviction | `agent.exit_score` | 0.00 | A holding whose score drops below this is sold (after the minimum hold) |
| Swap margin | `agent.swap_margin` | 0.25 | When full, a new idea must beat the weakest holding by this much |
| Min holding (days) | `agent.min_hold_days` | 5 | Score-based exits and swaps wait this long (stops do not) |
| Time stop (days) | `agent.max_hold_days` | 90 | A position still below its entry after this is sold |
| Initial stop (× ATR) | `agent.stop_atr` | 3.0 | First stop = entry − 3 × ATR |
| Trailing stop (× ATR) | `agent.trail_atr` | 4.0 | Stop follows highest close − 4 × ATR, never down |
| Skip gap-ups above % | `agent.max_gap_up_pct` | 3% | Don't chase stocks that open far above yesterday's close |
| Index regime filter | `agent.regime_filter` | off | When on, invest at most 40% while NIFTY is in a downtrend |
| Risk-off max exposure % | `agent.risk_off_max_exposure_pct` | 40 | Cap used when the filter is on |
| Claude reviews buys | `agent.llm_review` | on | Stage 3 (needs an API key) |

Not shown on the page, but used:

| Setting | Default | Meaning |
|---|---|---|
| `agent.cash_buffer_pct` | 2% | Cash always kept back |
| `agent.screen_weight` | 0.4 | Weight of the screen score in the final score |
| `agent.max_swaps_per_day` | 1 | Most swaps per day |
| `agent.entry_limit_buffer_pct` | 0.3% | How far above the live price the buy limit is set |

### Hard risk limits (every order)

| Label | Setting | Default | Meaning |
|---|---|---|---|
| Risk per trade % | `risk.risk_per_trade_pct` | 1.5% | Most a single stop-out should cost, as % of equity |
| Max position % of equity | `risk.max_position_pct` | 22% | One stock can't exceed this |
| Daily loss limit → kill switch % | `risk.max_daily_loss_pct` | 4% | A day down this much turns on the kill switch automatically |
| Orders per second (SEBI < 10) | `risk.max_orders_per_second` | 5 | Never above 9; SEBI requires algo registration at 10+/sec |
| Orders per day | `risk.max_orders_per_day` | 60 | Daily cap |
| Fat-finger limit % | `risk.fat_finger_pct` | 5% | A limit price more than 5% from the live price is refused |

Universe settings: **Min price ₹** 20 and **Min ₹ Cr traded/day** 5. Also `universe.top_n` = 750 and `universe.source` = top_liquid.

### Execution

| Field | Meaning |
|---|---|
| Mode | PAPER or LIVE |
| Broker | Paper simulator, groww or zerodha |
| Charges model | groww (used for paper fills and evaluation) |
| Claude | Model name and whether an API key was found |
| Order tag | `QDAGENT`: attached to every order for audit (SEBI requires algo orders to be identifiable) |

**Moving from paper to Groww:** the five steps for after the evaluation.

## B10. Status badges: what every status word means

**Orders:**

| Status | Meaning |
|---|---|
| OPEN | Waiting at the exchange (a limit not yet reached, or a stop not yet triggered) |
| COMPLETE | Executed |
| CANCELLED | Cancelled by the agent, or a DAY order expired by the broker at the close |
| EXPIRED | Still open in our records at the end of the day; marked expired |
| REJECTED | Refused by the broker (e.g. insufficient funds) |
| BLOCKED | Refused by **our own risk guard** before reaching the broker; the reason is shown |
| DECLINED | Live mode with manual confirmation, and you didn't type "yes" |

**Decisions:**

| Status | Meaning |
|---|---|
| planned | Made in the morning, waiting for the open |
| submitted | Order sent |
| skipped | Not executed (gap-up, no cash, no price); the reason is appended |
| failed | The order was blocked or rejected |
| void | The plan was made outside trading hours and never executed |

**Phases:**

| Status | Meaning |
|---|---|
| running | In progress |
| done | Completed |
| failed | Errored (retried up to 3 times) |
| skipped | Holiday, or started too late in the session |

**Evaluation checks:** pass / warn / fail / pending.

---

# Part C: The algorithms in detail

## C1. The universe: which stocks are considered

1. **The list.** NSE's official equity list (`EQUITY_L.csv`), keeping only **EQ series**, which means normal rolling settlement. BE/BZ "trade-for-trade" stocks are excluded. That gives about 2,333 stocks; it is refreshed every 7 days.
2. **Sectors.**
   - **NSE classification:** from the NIFTY Total Market and NIFTY 500 lists (~750 stocks).
   - **Yahoo's classification:** for all others, looked up once and cached.
3. **Point-in-time liquidity universe** (`universe.source: top_liquid`, `top_n: 750`). Each day, among stocks that pass the liquidity floor, only the **750 with the highest 60-day median traded value** (close × volume) are considered.

   This is computed from data up to that day only. In a backtest of 2024, the universe is 2024's most-traded stocks, not today's. Using today's index membership would be cheating, because stocks enter indices *after* they rise.

## C2. Price data and why it is "split-adjusted only"

- **Daily bars** (open, high, low, close, volume) for 4 years come from Yahoo Finance in batches of 200 tickers. They are stored as one wide table per field (dates × stocks).
- **Split-adjusted only.** When a stock splits 1:2, past prices are halved so charts stay continuous; that is necessary. They are **not** dividend-adjusted. Dividend adjustment rescales old prices using dividends paid *later*, which would leak future information into the past.
- **Re-download check.** Each morning only the last 10 days are downloaded. If those overlap stored prices but differ by more than 0.5% (Yahoo applied a new split), that stock's full history is re-downloaded.
- **No partial days.** Before 15:35 IST, today's in-progress bar is dropped (`completed()`), so decisions only use finished days.
- **Intraday prices** (for paper fills and the live tick) are 1-minute bars, cached for 20 seconds. Outside market hours, the last daily close is used.

## C3. Stage 1: the whole-market screener

This runs in about 0.4 seconds for 2,300 stocks (`quantdesk/trader/screener.py`). It is a pure function of past prices, and both the live agent and the backtest call it.

### Gates (a stock must pass all of them)

| Gate | Rule | Why |
|---|---|---|
| Fresh | Traded within the last 4 calendar days | Excludes suspended stocks |
| Price | Close ≥ ₹20 | Avoids penny stocks |
| Liquidity | 20-day median traded value ≥ ₹5 crore | Our orders must not move the price |
| History | ≥ 260 trading days of data | The factors need a year of history |
| Top-N | Among the 750 most-traded (60-day) | Point-in-time universe (C1) |
| **Uptrend** | Close > 200-day average **and** 50-day > 200-day average | Only buy stocks already going up |
| Not overextended | Close less than 25% above its 50-day average, and RSI(14) < 80 | Don't buy a stock that has gone vertical |
| Not too wild | ATR is less than 6% of price | Extremely volatile stocks blow through stops |

### Factors (each measured for every liquid stock)

| Factor | Formula | Weight | Idea |
|---|---|---|---|
| `mom_12_1` | Price 21 days ago ÷ price 252 days ago − 1 | 0.25 | 12-month momentum skipping the last month: one of the most documented effects in finance (Jegadeesh & Titman 1993). The last month is skipped because very short-term moves tend to reverse. |
| `mom_6_1` | Price 21 days ago ÷ price 126 days ago − 1 | 0.20 | Medium-term momentum |
| `risk_adj_mom` | `mom_12_1` ÷ 63-day annualised volatility | 0.20 | Prefers steady climbers over erratic ones |
| `high_52w` | Close ÷ highest high of the last 252 days | 0.15 | Stocks near their 52-week high tend to keep going (George & Hwang 2004) |
| `low_vol` | −(63-day annualised volatility) | 0.10 | Low-volatility stocks have historically had better risk-adjusted returns |
| `volume_trend` | ln(average volume of 20 days ÷ average of 120 days) | 0.05 | Rising participation |
| `short_reversal` | −(5-day return) | 0.05 | Slight penalty for stocks that just spiked |

### Scoring

1. **z-score each factor.** Each factor is turned into a **z-score** across the liquid stocks: z = (value − average) ÷ standard deviation. It is then **winsorised** to ±3, meaning capped so one outlier can't dominate.
2. **Raw score** = Σ (weight × z) ÷ Σ weights.
3. **Screen score** = 2 × percentile − 1. The best stock gets about +1, the median 0, the worst about −1. A screen score of +0.96 means the stock beats 98% of the liquid universe.
4. **Rank:** 1 = highest raw score.
5. **Shortlist:** the top 25 **eligible** stocks by raw score go to stage 2.

## C4. Market regime and breadth

- **Regime** (NIFTY 50):
  - **risk-on** if close > 200-day average **or** 50-day > 200-day.
  - **risk-off** only when both are broken.
  - With `regime_filter: true`, risk-off caps total investment at 40% of equity. It is **off** by default: in the out-of-sample test, the filter reduced returns more than it reduced drawdowns.
- **Breadth** = % of liquid stocks above their own 200-day average. It is shown for context and not used in decisions.

## C5. Stage 2: deep research on the shortlist

For each of the 25 shortlisted stocks, plus every current holding and any watchlist stocks, the agent runs `analyze_symbol` (`quantdesk/engine/analyzer.py`). The stocks are analysed four at a time in parallel.

It produces:
- six **model votes** (C6), combined into a **deep score**
- a 5-day **forecast ensemble** (C7)
- a **GARCH** volatility forecast
- a walk-forward **ML** probability (C8)
- **statistical diagnostics** and a **regime** label (C10)
- a 1-year **risk profile** (return, volatility, Sharpe, drawdown, VaR)

**News** for all of these stocks is fetched in parallel, and the headlines are scored together in one batch (C9).

## C6. The six model votes, one by one

Every vote is squashed into −1…+1. `tanh` is the hyperbolic tangent: it maps any number smoothly into −1…+1, so extreme readings can't dominate.

**1. Trend** (weight 0.25):

```
raw    = [ sign(close − SMA200) + sign(SMA50 − SMA200) + tanh((+DI − −DI)/20) ] / 3
trend  = raw × (0.5 + 0.5 × clip(ADX/40, 0, 1))
```

- The **SMAs** check where price is against its long-term averages.
- **±DI** (directional indicators) check whether recent highs or lows dominate.
- **ADX** measures trend *strength*: a strong trend (ADX ≥ 40) gets the full vote, a weak one half.

**2. Momentum** (weight 0.20):

```
momentum = 0.7 × tanh( 12-1 month return / annual volatility ) + 0.3 × tanh( 2 × MACD histogram / ATR )
```

- The first term is risk-adjusted long-term momentum.
- The second is short-term impulse. **MACD** = 12-day EMA − 26-day EMA; the histogram is MACD minus its 9-day EMA.

**3. Mean reversion** (weight 0.15):

```
mr   = 0.5 × clip(−(RSI14 − 50)/25, −1, 1) + 0.5 × clip(−(%B − 0.5) × 2, −1, 1)
vote = mr × (1 − 0.6 × trend strength)
```

- This is the contrarian view: an oversold stock (low **RSI**, price near the lower **Bollinger band**, %B ≈ 0) votes up; an overbought one votes down.
- It is weakened in strong trends, because fighting a strong trend is expensive.
- This vote often disagrees with momentum. That's deliberate: it stops the agent buying the most stretched names.

**4. Forecast** (weight 0.15):

```
forecast = 0.5 × tanh( Kalman slope t-stat / 3 ) + 0.5 × tanh( 2 × ensemble 5-day t-stat )
```

This combines a noise-filtered trend estimate with the forecast ensemble (C7).

**5. ML** (weight 0.15):

```
ml = (2 × P(up) − 1) × skill,   skill = clip( (out-of-sample AUC − 0.5) / 0.05, 0, 1 )
```

- A model with no proven out-of-sample skill (AUC ≤ 0.5) gets **zero** say.
- Full say only at AUC ≥ 0.55.

**6. Sentiment** (weight 0.10):

```
sentiment = news score × confidence
```

It is missing (and simply left out) when there are no headlines.

**Deep score** = weighted average of the votes that exist:

```
deep = Σ (weight × vote) / Σ (weights of available votes)
```

## C7. Forecasting models (ARIMA, ETS, Kalman, GARCH)

All forecast the **cumulative log-return over 5 trading days** with an uncertainty band.

- **ARIMA(p,1,q).** It models daily returns as depending on their own recent values (AR terms, p) and recent forecast errors (MA terms, q). All combinations of p, q ∈ {0,1,2} are tried on the last 750 days; the one with the lowest **AIC** wins. AIC rewards fit and penalises complexity.
- **ETS (Holt's damped trend).** Exponential smoothing of log-price, with a level and a trend that is "damped" so it doesn't extrapolate forever. Fitted on the last 500 days.
- **Kalman filter (local linear trend).** It treats the true price level and its daily drift (slope) as hidden quantities. Each day it *predicts* them, sees the actual price, and *corrects* by the Kalman gain, which weighs how much to trust the new price versus the prediction. The result is a noise-robust estimate of the drift and of how certain it is (the slope's t-stat).
- **Ensemble.** The three forecasts are averaged with **inverse-variance weights**: models that are more certain count more. The ensemble reports:
  - expected return
  - standard deviation
  - **t-stat** = expected ÷ std
  - **P(up)** = Φ(t), the normal cumulative probability

  Daily stock forecasts usually have |t| < 0.5. That is honest: short-term returns are mostly noise.
- **GARCH(1,1) with Student-t errors** (last 1,000 days). It models tomorrow's variance as:

  ```
  σ²(t) = ω + α ε²(t−1) + β σ²(t−1)
  ```

  Calm periods and storms cluster, and **persistence α + β** (close to 1) says how long a volatility shock lasts. Volatility, unlike returns, *is* fairly predictable. This forecast feeds sizing in the analyzer and is shown on the stock page.

## C8. Machine learning (walk-forward gradient boosting)

- **Inputs (features)**, all computed from past data only:
  - **Returns:** over 1, 5, 10, 21 and 63 days.
  - **Volatility:** realised volatility over 10, 21 and 63 days.
  - **Oscillators:** RSI(14), RSI(2), MACD histogram ÷ price, Bollinger %B and band width.
  - **Range and averages:** ATR ÷ price, and distance from the 20/50/200-day averages.
  - **Trend indicators:** ADX and the ±DI spread, stochastic %K.
  - **Volume:** 21-day volume z-score, 21-day OBV slope.
  - **Day of week.**
- **Target.** Is the price higher in 5 trading days? (1 = yes, 0 = no.)
- **Model.** Histogram gradient-boosted trees: depth 3, learning rate 0.05, 200 trees, at least 40 samples per leaf, L2 regularisation 1.0.
- **Purged walk-forward validation**, the honest way to test time-series ML:
  1. Train on data up to 5 days before the test block starts. The 5-day "purge" matters because each label looks 5 days ahead, so overlapping labels would leak the answer.
  2. Predict the next 63 days (about a quarter).
  3. Move forward 63 days, retrain, and repeat. The first test starts after 500 days of training data.
- **What it reports:**
  - **accuracy** vs the **naive base rate** (always predicting the majority outcome)
  - **AUC**
  - **Brier score** (how calibrated the probabilities are)
- **Today's P(up)** comes from a final model trained on all labelled history.
- **Skill gating.** Its vote is scaled by its out-of-sample AUC (C6). Never-tested-on-unseen-data accuracy is never trusted.

## C9. News and sentiment (lexicon + Claude)

1. **Collect.**
   - **Google News RSS** search for the company name in quotes (e.g. "Laurus Labs"), over the last 7 days, plus Yahoo Finance news for the ticker.
   - Google results are kept only if the headline contains the symbol or the company's first distinctive word.
   - Near-duplicates (same title from syndication) are removed. Up to 12 headlines per stock.
2. **Score each headline.**
   - **With Claude** (an API key in `.env`):
     - **Request:** all headlines go in one batch, and the model returns structured JSON: **sentiment** (−1…+1, the likely 1–5-day price impact on *that* stock), **relevance** (0…1), **impact** (low/medium/high) and an **event type** (earnings, guidance, order win, regulatory…), with a one-line rationale.
     - **Safety:** headlines are treated as untrusted text, so instructions inside them are ignored.
     - **Caching:** results are stored per headline and never paid for twice.
   - **Without Claude**, a finance-specific **lexicon**: lists of positive and negative words and phrases ("beats estimates" +2, "SEBI probe" −2, "profit falls" −2…), with negation handling ("not facing any probe" flips the sign). Lexicon headlines get relevance 0.6 and impact medium if |score| > 0.4, else low.
3. **Aggregate.**

   ```
   weight_i  = 0.5^(age_hours / 36) × relevance_i × impact_i      (impact: low 0.5, medium 1.0, high 1.6)
   score     = Σ weight_i × sentiment_i / Σ weight_i
   confidence = min(1, Σ weight_i / 3)
   ```

   - **Recency:** news halves in importance every 36 hours.
   - **Confidence:** reaches 1 with about three fresh, relevant, medium-impact headlines.
4. **Veto.** If score × confidence ≤ −0.4, the stock is excluded from buying that day ("strongly negative news").

## C10. Statistical diagnostics and regimes

Shown on stock pages; used for understanding, not as buy rules.

| Test | What it tells you |
|---|---|
| **Hurst exponent** | How the spread of k-day price changes grows with k. H ≈ 0.5 is a random walk, > 0.55 trending, < 0.45 mean-reverting; this gives the "Character" label. |
| **ADF test** | Is the series stationary (does it return to a mean)? Prices almost never are; returns almost always are. |
| **Variance ratio** | Variance of 5-day returns ÷ 5 × variance of 1-day returns. Above 1 is trending, below 1 mean-reverting. |
| **Ljung-Box** | Are past returns correlated with future returns (any predictability)? |
| **Jarque-Bera** | Are returns normally distributed? (Almost never: they have "fat tails".) |
| **OU half-life** | If prices revert, how many days a deviation takes to halve |
| **Regime (GMM)** | A Gaussian mixture model clusters each day's 21-day return and volatility into 3 groups, named by their average return: *bull*, *sideways*, *bear/quiet* or *bear/stress*. |

## C11. Final conviction score and vetoes

```
final = 0.4 × screen score + 0.6 × deep score
```

A stock becomes a **candidate** if it is on the shortlist, its research succeeded, and it has no veto (news, C9). It can be **bought** only if its final score is ≥ **0.30** and the portfolio rules allow it (C13).

## C12. Stage 3: Claude's review

- **When:** if `agent.llm_review` is on and an API key exists.
- **What goes in:** the strongest buy candidates (up to 2 × max positions = 12) are sent to Claude, along with all their evidence: score, rationale, forecast, ML numbers and recent headlines.
- **Instructions:** Claude acts as a supervising portfolio manager. It approves unless it sees a concrete reason the models can't:
  - an upcoming binary event (results, a court ruling, lock-in expiry)
  - governance red flags (fraud, auditor resignation, promoter pledging)
  - a one-off cause behind the price move
  - data that looks wrong

  It is told **not** to reject just because a stock "rose a lot", because the strategy is momentum by design.
- **Can only remove.** Claude can **remove** a buy, never add one. Its verdict, confidence and reasoning are stored and shown on the stock page.
- **Default model:** `claude-opus-5-5`. If a request is refused for safety reasons, Anthropic's server automatically retries it on a fallback model.
- **Without a key:** the step is skipped and logged, and the quantitative decision stands.

## C13. The portfolio policy: exits, entries, swaps, sizing

This is `plan_day` in `quantdesk/trader/policy.py`: pure logic, called identically by the live agent and the backtest. It runs every morning on yesterday's closing data.

### Step 1: exits (decided in the morning, executed at the open)

A holding is sold if any of these is true:

| Rule | Condition |
|---|---|
| **Trend broken** | Held ≥ 5 days and yesterday's close was below its 200-day average |
| **Conviction decayed** | Held ≥ 5 days and today's final score < 0.00 |
| **Time stop** | Held ≥ 90 days and still below the entry price |

Stop-losses are not part of the plan. They sit at the exchange all day (C14).

### Step 2: room

```
slots = 6 − holdings remaining
cash  = current cash + expected proceeds of today's sells − 2% buffer
```

### Step 3: entries

Candidates are taken from the best final score down, and each is checked:

1. **Score:** skip if already held or the score is < 0.30.
2. **Full portfolio:** if there's no free slot, look at the **weakest** holding (held ≥ 5 days). If the candidate's score beats it by at least **0.25**, and fewer than 1 swap was made today, plan to sell the weakest and buy the candidate ("replaced by X"). The swap is committed only if the new buy passes every other check, so the agent never sells something for nothing.
3. **Sector cap:** at most 2 stocks per sector.
4. **Size:** compute the quantity (below). If the position would be under ₹5,000, skip it ("position too small").
5. **Regime:** if the regime filter is on and the market is risk-off, stop buying once 40% is invested.

Up to 15 skipped candidates are recorded with their reasons.

### Position sizing

```
target value  = min( equity / 6 ,  equity × 22% × 0.98 )
value cap     = min( target value, cash available, exposure room )
qty by value  = floor( value cap / price )
qty by risk   = floor( equity × 1.5% / (3 × ATR) )
quantity      = min( qty by value, qty by risk )
```

**Worked example** (equity ₹50,000):

- **Target value:** min(₹8,333, ₹10,780) = ₹8,333.
- **Stock A** (price ₹1,000, ATR ₹30): qty by value = 8, and qty by risk = floor(750 / 90) = 8. **8 shares = ₹8,000.** If the stop (₹910) is hit, the loss is about ₹720, roughly 1.5% of equity.
- **Stock B** (price ₹500, ATR ₹40, very volatile): qty by value = 16, qty by risk = floor(750 / 120) = 6. **6 shares = ₹3,000**, which is below the ₹5,000 minimum, so it's **skipped**. Wildly volatile stocks get small positions or none.

**ATR (Average True Range)** is the average daily price range over 14 days, using Wilder smoothing. It includes gaps from the previous close. ATR is the "normal daily wiggle" of a stock in rupees.

## C14. Stops: initial and trailing

- **Initial stop** = entry price − 3 × ATR, rounded down to the ₹0.05 tick.
- **Trailing stop**, raised at every day's close:

  ```
  new stop = max( old stop, highest close since entry − 4 × ATR )
  ```

  The stop **only ever moves up**. As a stock rises, the stop follows it and locks in profit. A sell triggered above the entry price is labelled "trailing stop (profit locked)".
- **How it's enforced.** Every morning (DAY validity), a real **SL-M order** (stop-loss market) is placed for each holding at the stop price. The exchange, or the paper simulator, sells automatically if the price trades at or below it, even if the agent's computer is busy. If the stock opens *below* the stop (a gap down), it sells at the open, which is worse than the stop. That is the real risk of gaps.

## C15. Execution: the open, the ticks, the close

### Open (09:20)

1. **Holiday check.** The agent confirms NIFTY has printed today. If there are still no prints by 09:45, the day is treated as a holiday and skipped.
2. **Sells first.** For each planned sell, any resting stop is cancelled, then a **MARKET** sell is sent for the held quantity.
3. **Then buys.** For each planned buy:
   - **Gap filter:** get the live price; if it is more than **3% above yesterday's close**, skip.
   - **Limit price** = live price × 1.003, rounded **up** to ₹0.05. That is marketable, so it fills almost immediately, but it protects against a sudden spike.
   - **Quantity:** recomputed as min(planned qty, (cash − 2% buffer) ÷ limit). If the value is below ₹5,000, skip.
   - **Send:** the LIMIT buy goes through the risk guard.

### Tick (every 60 s, 09:15–15:30)

1. **Paper fills.** In paper mode, the simulator matches open orders against new 1-minute bars (C16).
2. **Sync.**
   - **Order statuses:** pulled from the broker; fills are logged.
   - **New positions:** a newly filled entry becomes a managed position with its initial stop.
   - **Closed positions:** a position that disappeared, because a stop or exit filled, becomes a closed **trade** with its P&L and exit reason.
   - **Quantity changes:** an unexplained change (split or bonus) rescales prices.
3. **Ensure stops.** Every holding must have exactly one resting SL-M at its current stop price and quantity; it is placed or replaced if needed.
4. **Snapshot.** The account snapshot is saved for the website, and every 5 minutes an intraday equity point.

### Cancel (15:10)

Buy orders that still haven't filled are cancelled. No chasing late in the day.

### Close (15:45)

1. **Expire.** The paper broker expires DAY orders, and applies corporate actions (dividends credited, splits and bonuses applied).
2. **Raise trailing stops.** Today's close sets the highest close since entry, and each stop is raised (C14).
3. **Record the day.** A daily **NAV** row is written: equity, cash, invested, number of positions, NIFTY 50 and NIFTY 500 closes. This is the evaluation data.
4. **Journal.** The `eod` event: equity, return since inception, stops raised.

## C16. The paper exchange: how fills are simulated

The simulator is strict on purpose, so paper results don't flatter the agent. Orders only fill **during market hours**, against **1-minute bars that trade after the order was placed**, never earlier ones.

| Order type | Fill rule |
|---|---|
| **MARKET** | Placed during hours: fills at the latest price + 5 bps slippage (0.05%, against you). Placed while closed: fills at the next session's first bar's **open** + slippage. |
| **LIMIT buy** | Fills only when a bar's **low ≤ limit**, at min(limit, bar open). If the market gaps below the limit, you get the better open. A marketable limit fills immediately at the live price. |
| **LIMIT sell** | Mirror image: high ≥ limit, at max(limit, open) |
| **SL-M sell (stop)** | Triggers when a bar's **low ≤ trigger**. Fills at min(trigger, bar open) − slippage, so a gap through the stop fills at the worse price. |

**Other rules:**
- **Cash:** checked at fill time; a buy without enough cash is REJECTED.
- **No short selling:** selling more than held is REJECTED.
- **DAY orders:** expire at the close.
- **Charges:** every fill pays the Groww charges (C17).
- **Corporate actions:**
  - **Dividends:** credited as cash on the ex-date.
  - **Splits/bonuses:** multiply the quantity and divide the average price.

## C17. Charges and taxes (with a worked example)

Indian delivery (CNC) charges, using the Groww profile:

| Charge | Rate |
|---|---|
| Brokerage | 0.1% of order value, minimum ₹5, maximum ₹20, per order |
| STT (securities transaction tax) | 0.1% on buy **and** sell |
| Exchange transaction charge (NSE) | 0.00297% |
| SEBI turnover fee | ₹10 per crore (0.0001%) |
| Stamp duty | 0.015% on **buy** only |
| GST | 18% on (brokerage + exchange charge + SEBI fee) |
| DP charge | ₹16.52 per stock per **sell** day (depository fee, incl. GST) |

**Worked example: buy ₹8,000 of a stock, then sell it for ₹8,000:**

| | Buy | Sell |
|---|---|---|
| Brokerage | ₹8.00 | ₹8.00 |
| STT | ₹8.00 | ₹8.00 |
| Exchange | ₹0.24 | ₹0.24 |
| SEBI | ₹0.01 | ₹0.01 |
| Stamp | ₹1.20 | – |
| GST | ₹1.48 | ₹1.48 |
| DP | – | ₹16.52 |
| **Total** | **₹18.93** | **₹34.25** |

A round trip costs **about ₹53, or 0.66%**. A stock must rise about 0.66% just to break even. This is why positions under ₹5,000 are skipped, and why the agent avoids churning.

**Tax** (shown in backtests, not deducted from equity):
- **STCG:** 20% on gains from stocks held ≤ 12 months.
- **LTCG:** 12.5% on gains above ₹1.25 lakh a year for stocks held > 12 months.

## C18. The risk guard: checks on every single order

Every order, paper or live, from any phase, passes `RiskGuard.check` before it can reach the broker. Any failed check **blocks** the order (status BLOCKED, reason logged).

1. **Kill switch.** If on, every **BUY** is blocked. Sells and stops still go through (close-only).
2. **Live interlocks** (real brokers only):
   - `execution.mode` must be `live`.
   - `.env` must contain `QD_LIVE_CONFIRM=I_UNDERSTAND_THE_RISK`.
   - The market must be open.
3. **Market hours.** Applies to the agent in paper mode too, for parity.
4. **Quantity** > 0.
5. **No short selling.** A delivery SELL can't exceed the shares held.
6. **Fat-finger.** A limit price more than 5% away from the live price is refused.
7. **Buys only:**
   - **Position size:** the position after the buy must be ≤ 22% of equity.
   - **Gross exposure:** total stock exposure must stay ≤ 100% (no leverage).
   - **Open positions:** at most 8 different stocks (a safety margin above the policy's 6).
   - **Daily loss:** if equity is down ≥ 4% since the day began, the **kill switch turns on automatically** and the buy is blocked.
8. **Rate limits.**
   - **Per second:** at most 5 orders in any 1-second window (hard ceiling 9; SEBI requires a registered algorithm at 10 or more per second per exchange).
   - **Per day:** at most 60 orders.

Each check's outcome is written to the audit trail.

## C19. Accounting: the virtual sub-account

The agent tracks its own money separately from whatever else is in the broker account:

```
virtual cash = inception capital
             + Σ realised P&L of the agent's closed trades (after charges)
             − cost of the agent's open positions (qty × entry price)
             − estimated buy charges of those positions
equity       = virtual cash + Σ qty × current price  (agent positions only)
cash used    = min(virtual cash, broker's actual available cash)
```

**Trade P&L:**

```
P&L = (exit price − entry price) × qty − buy charges − sell charges
```

**Why:** when connected to your real Groww account, the agent ignores your personal holdings and never spends more than its allocated capital. On paper, the virtual equity equals the paper broker's equity, and a test checks this. The one exception is dividends, which only the paper broker counts, so the evaluation is slightly conservative.

## C20. The evaluation maths

Daily returns r(t) come from the daily NAV rows. NIFTY is normalised to the same starting capital.

| Metric | Formula |
|---|---|
| Total return | Equity_T ÷ capital − 1 |
| Excess return | Equity_T ÷ NIFTY-equivalent_T − 1 |
| Max drawdown | min over t of ( Equity_t ÷ max(Equity up to t) − 1 ) |
| Annual volatility | std(r) × √252 |
| **Sharpe** | (mean(r) − 6.5%/252) ÷ std(r) × √252. 6.5% is roughly the Indian T-bill yield (the "risk-free" return). |
| Sortino | Like Sharpe, but divides by downside deviation only |
| Beta | cov(r, r_NIFTY) ÷ var(r_NIFTY). Beta 1.2 means the agent moves about 1.2% when NIFTY moves 1%. |
| Alpha | (mean(r) − beta × mean(r_NIFTY)) × 252: yearly return not explained by market exposure |
| Information ratio | mean(r − r_NIFTY) ÷ std(r − r_NIFTY) × √252 |
| **Alpha t-stat** | mean(r − r_NIFTY) ÷ std(r − r_NIFTY) × √(number of days). Above ~2 suggests the edge over NIFTY is unlikely to be luck; below that it could be. |
| CAGR | (Equity_T ÷ capital)^(1/years) − 1, shown only after 60+ days |
| Monthly return | Month-end equity ÷ previous month-end (or capital) − 1 |

Why ~120 days are needed: daily returns are noisy. With a typical daily tracking difference, an alpha t-stat of 2 needs either a large edge or many days. Short records can look brilliant or terrible purely by chance.

## C21. The backtest engine and how it avoids the future

`quantdesk/trader/backtest.py` replays the **same `screen()` and `plan_day()`** day by day.

```
for each trading day t:
   1. at t's OPEN: execute the plan decided on day t−1's close
        sells at open × (1 − 0.05%)
        buys at open × (1 + 0.05%), unless open > yesterday's close + 3% (gap filter)
   2. during t: a position whose LOW ≤ its stop is sold at min(open, stop) × (1 − 0.05%)
   3. at t's CLOSE: mark to market, raise trailing stops, record equity
   4. decide for t+1 using ONLY data up to t's close (asserted in code)
```

**Guarantees against look-ahead:**
- **Point-in-time view:** decisions receive `panel.until(t)`, a copy of the data that physically ends at day t. The code asserts that its last row is t.
- **Next-open execution:** fills use the next day's open; stops use that day's low. A decision can never trade at a price that existed before the decision.
- **Point-in-time universe:** top-750 by trading value as of each day (C1), not today's index list.
- **Split-only prices** (C2).
- **Proven by a test.** `tests/test_backtest_causality.py` runs a backtest, then **scrambles every price after a cutoff date** and runs it again. Every buy, sell and equity value before the cutoff must be identical, and they are. If any decision peeked at future prices, scrambling the future would change the past.

**What's not replayed:**
- **Research layers:** the deep research layer, news, and Claude (too slow, no news archive). The backtest therefore uses the screen score as the final score.

**Remaining bias:**
- **Delisted stocks:** stocks that delisted before today are missing from NSE's current list. Survivors are over-represented, which flatters results.

**Minimum capital** = ₹5,000 ÷ 22% ≈ ₹22,727, rounded up to **₹23,000**. Below this, a position large enough to be worth its charges would exceed the per-stock limit, so the agent can't trade at all.

**Path dependence.** Results depend heavily on *which* stocks fill the six slots, which is partly luck of timing. The same rules over Jan 2024 – Oct 2026 show +54% as one run. The two halves run separately (+28.3%, then −3.9%) compound to only about +23%. Treat any single backtest number as rough.

## C22. The tamper-evident ledger

Every event row stores:

```
hash = SHA-256( previous row's hash | time | account | level | kind | message | data )
```

The first row chains from "GENESIS". Changing any old row changes its hash, which no longer matches the `prev_hash` stored in the next row. **Verify integrity** (or `qd verify`) recomputes the whole chain and reports the first broken row. This makes the six-month record checkable: silently rewriting history would be detected.

## C23. The autopilot scheduler and crash recovery

- **Loop.** `qd autopilot` loops forever (every 60 s, or the tick interval during market hours). Each pass:
  1. Writes a **heartbeat**, which drives the "Agent running" dot.
  2. Runs whichever phases are due and not yet done. Each phase's status is stored per day, so a restart continues exactly where the day stands.
- **Late starts:**

  | When the agent starts | What happens |
  |---|---|
  | before 14:45 | Research runs as catch-up, and the open phase still executes |
  | after 14:45 | The open is marked "skipped (too late)" |
  | any time | The close (EOD) always runs |
- **Failures.** A failed phase is retried, up to 3 times per day. A failed tick never stops the next one; risk management continues.
- **One autopilot only.** An OS-level file lock (`data/autopilot.lock`) prevents a second autopilot, because two would double every order. `qd start` also detects an already-running web app and reuses it, or picks the next free port if 8000 is taken.
- **Supervisor.** `qd start` runs the autopilot and the web server as two processes and restarts either if it dies. If one keeps crashing immediately, it backs off for 60 seconds instead of spinning.
- **Restart after code updates.** Running processes keep the code they started with.

---

# Part D: Glossary

| Term | Meaning |
|---|---|
| **ATR** | Average True Range: the stock's typical daily move in ₹ over 14 days, including gaps |
| **AUC** | Area under the ROC curve: how well a model ranks ups above downs. 0.5 = random. |
| **Base rate (naive)** | Accuracy from always guessing the most common outcome; a model must beat it |
| **Bollinger bands / %B** | 20-day average ± 2 standard deviations; %B is where price sits between them (0 = lower band, 1 = upper) |
| **Breadth** | Share of stocks above their 200-day average |
| **CNC** | Delivery trade: shares are bought and held in your demat account |
| **Conviction / final score** | 0.4 × screen + 0.6 × deep, from −1 to +1 |
| **DAY order** | An order valid only until the session closes |
| **Drawdown** | Fall from the most recent peak |
| **EMA / SMA** | Exponential / simple moving average |
| **Equity** | Cash + market value of positions |
| **Excess return** | Return beyond what NIFTY delivered over the same period |
| **Gap** | Opening price far from the previous close |
| **Kill switch** | Close-only mode: no new buys |
| **LIMIT order** | Buy at or below (sell at or above) a set price |
| **LTP** | Last traded price |
| **Look-ahead bias** | When a backtest uses information that wasn't available at the time; makes results falsely good |
| **MARKET order** | Execute immediately at the best available price |
| **Momentum (12-1)** | Return over the past 12 months excluding the latest month |
| **NAV** | Net asset value: the account's total worth at a day's close |
| **NIFTY 50** | Index of 50 large Indian companies; the benchmark |
| **Percentile** | Share of stocks with a lower score |
| **Purging** | Leaving a gap between training and testing data so overlapping labels can't leak |
| **Realised / unrealised P&L** | Locked in by selling / on paper while still held |
| **RSI** | Relative Strength Index (0–100): above 70 overbought, below 30 oversold |
| **Sharpe ratio** | Return above the risk-free rate per unit of volatility, annualised |
| **Slippage** | The difference between the expected price and the actual fill (modelled as 0.05%) |
| **SL-M** | Stop-loss market order: becomes a market sell once the price touches the trigger |
| **Stop-loss / trailing stop** | Automatic exit price, fixed at first, then rising with the price |
| **Survivorship bias** | Testing only on companies that still exist, which ignores the failures |
| **Swap** | Selling the weakest holding to buy a clearly stronger idea |
| **t-stat** | Estimate ÷ its uncertainty; above ~2 is unlikely to be pure chance |
| **Volatility** | How much a price fluctuates; annualised standard deviation of returns |
| **Walk-forward** | Repeatedly training on the past and testing on the following unseen period |
| **Winsorise** | Cap extreme values (here at ±3 standard deviations) so outliers can't dominate |
| **z-score** | (value − average) ÷ standard deviation: how unusual a value is |

*Further reading on the general maths (indicators, risk metrics, portfolio theory, options): `docs/MATH_GUIDE.md`.*

# QuantDesk: an autonomous trading agent for NSE equities

QuantDesk scans the whole Indian stock market every morning, researches the strongest candidates in depth, decides what to buy and sell, and trades on its own. It manages every position with a resting stop-loss. Paper trading and real trading run **the exact same code**; the only difference is which broker object the agent is handed.

The plan: run it on paper with ₹50,000 for six months, judge it against NIFTY 50 on the Evaluation page, and only then connect Groww.

> Personal use only. Models are probabilistic and can lose money. Nothing here is investment advice.

> **Full guide:** [docs/GUIDE.md](docs/GUIDE.md) explains every page, number and parameter of the web app, and every algorithm in detail.

## How it trades (every market day, IST)

| Time | Phase | What happens |
|---|---|---|
| 08:15 | **Research** | Updates prices for the ~750 stocks in the NIFTY Total Market index (set `universe.source: nse_all` for all ~2,300 NSE equities). Screens them: liquidity → uptrend → not overextended → factor ranking (12-1 momentum, 52-week-high proximity, risk-adjusted momentum, low volatility…). The top 25 plus every holding get **deep research**: ARIMA/ETS/Kalman forecasts, GARCH volatility, purged walk-forward ML, statistics, news sentiment. **Claude** can then veto buys for reasons the models can't see. The portfolio policy builds the plan: sells, buys, sizes, stops. |
| 09:20 | **Open** | Confirms the market is trading (detects holidays). Executes exits, then entries as limit orders. Skips any stock that gapped up more than 3%. |
| every minute | **Manage** | Syncs fills, keeps a protective SL-M stop order resting for every holding, and snapshots equity. |
| 15:10 | **Cancel** | Cancels entries that never filled. |
| 15:45 | **Close** | Reconciles positions, raises trailing stops, records NAV vs NIFTY, and writes the journal. |

**Portfolio rules** (in `config/settings.yaml` under `agent:`):
- **Size:** at most 6 positions and 2 per sector, with a ₹5k minimum position. Each trade risks 1.5% of equity, measured to a 3×ATR stop.
- **Exits:** a 4×ATR trailing stop; a holding is sold if it falls below its 200-day average or its conviction score drops below zero (after 5 days); a 90-day time stop applies to losers.
- **Turnover:** a new idea must beat the weakest holding by 0.25 to replace it, at most one swap per day.
- **Market regime filter:** available but off by default (see below). When on, the agent invests at most 40% of the account while NIFTY is in a downtrend.

**Hard limits on every order** (`risk:`):
- **Daily loss limit:** a 4% daily loss engages the kill switch.
- **Order rate:** at most 5 orders/sec (hard cap 9, under SEBI's 10/sec registration threshold) and 60 per day.
- **Price and position checks:** a fat-finger price check and no short selling.
- **Kill switch:** close-only mode. It blocks buys but never blocks exits or stop-losses.

## Paper = live, by construction

```
TradingAgent ──► OrderExecutor ──► RiskGuard ──► Broker
                                                 ├─ PaperBroker   (evaluation: exchange simulator on real 1-min bars)
                                                 ├─ GrowwBroker   (after you trust it)
                                                 └─ ZerodhaBroker
```

The paper broker is deliberately strict:
- **Limit orders** fill only when the market trades through the limit.
- **Stop orders** trigger on the bar's low; if the price gaps through, they fill at the worse of the trigger and the open.
- **Charges:** Groww's full charges on every fill (brokerage, STT, stamp duty, exchange, SEBI, GST, DP).
- **End of day:** DAY orders expire, and splits, bonuses and dividends are applied.

The agent keeps its own **sub-account**: inception capital plus the realised P&L of its own trades. When it later shares your Groww account, it never touches holdings it didn't buy.

Every decision, order and fill goes into `data/quantdesk.db`. The event log is **hash-chained**, so the six-month record can be verified as unedited (`qd verify`, or the Activity page).

## Setup

```powershell
cd X:\stock_price
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[garch,dev]"
cd web; npm install; npm run build; cd ..     # the web app (already built in web/dist)
copy .env.example .env                        # optional: ANTHROPIC_API_KEY turns on Claude review + news scoring
pytest                                        # offline test suite
qd start                                      # autopilot + web app at http://localhost:8000
```

**Leave it running.** `scripts\install_autostart.ps1` registers `qd start` to launch at every Windows logon. Also stop the PC from sleeping during market hours. If the PC was off, the agent catches up when it next starts: research runs late, the open phase runs if the session is still young enough, and the close always runs.

## Commands

```powershell
qd start                    # everything (restarts either process if it crashes)
qd autopilot | qd serve     # the two halves separately
qd premarket                # run today's research + plan now (no orders)
qd status                   # equity, positions, scorecard
qd backtest-agent --start 2024-01-01      # replay the core policy over history
qd verify                   # check the ledger hasn't been tampered with
qd kill on|off              # close-only mode
qd paper reset --capital 50000            # start a fresh evaluation (logged)
qd analyze TCS | qd news TCS              # look at any single stock
qd broker login groww | qd broker test groww
```

## The web app (http://localhost:8000)

| Page | What it shows |
|---|---|
| **Overview** | Equity vs NIFTY, today's phases, holdings, live activity, latest decisions with reasons |
| **Portfolio** | Positions with entry thesis and stop distance, closed trades with exit reasons, sector allocation |
| **Research** | The daily funnel, market regime, the plan, and every researched stock with its scores |
| **Stock** (search any NSE symbol) | Candles with the agent's trades and stop line, model votes, forecasts, ML, news, Claude's verdict |
| **Activity** | Hash-chained event log and order history |
| **Evaluation** | Return vs NIFTY, excess return and alpha t-stat, Sharpe, drawdowns, monthly table, trade statistics |
| **Backtest** | Historical replay of the same policy, with caveats |
| **Settings** | Active limits, and the paper-to-Groww checklist |

## Backtesting without knowing the future

`qd backtest-agent` and the **Backtest** page replay the agent's screen and portfolio rules day by day, using the same code as live. The page streams a live log: each buy, each sell with buy price, sell price, P&L after charges, and NIFTY over the same days. The equity-vs-NIFTY chart grows as the replay runs.

**How look-ahead is prevented:**
- **Decisions use only past data:** each decision is computed from a copy of the data that physically ends at that day's close (asserted in code).
- **Next-day execution:** orders fill at the next day's open; stops trigger on that day's low.
- **Point-in-time universe:** the top 750 most-traded stocks *as of each day*. Today's index membership is not used, because stocks join indices *because* they went up.
- **Split-only price adjustment:** dividend adjustment would leak later dividends into past prices.
- **A test enforces it:** `tests/test_backtest_causality.py` scrambles every price after a date and checks that every earlier trade and equity value is identical.

**Not replayed:**
- **Research layers:** ML/forecasts (too slow at this scale), news (no archive) and Claude.

**Remaining bias:**
- **Delisted stocks:** stocks delisted before today are missing from NSE's current list.

**Minimum capital: about ₹23,000.** Each position must be at least ₹5,000, because fixed charges eat smaller trades, and at most 22% of the account. Below that floor the agent cannot open anything. That's why a ₹10,000 backtest shows a flat line; the app now rejects it with an explanation.

## How the defaults were chosen (and what to expect)

The defaults were chosen on a **training** period only, then checked on an **unseen** period. All runs used the clean, no-look-ahead setup above:

| Variant | Train: Jan 2024 – Jun 2025 | Unseen: Jul 2025 – Oct 2026 |
|---|---|---|
| NIFTY 50 buy-and-hold | +17.4% | −13.0% |
| Original rules (1% risk, 2.5/3×ATR stops, regime filter) | −12.1% | −6.8% |
| **Default** (1.5% risk, 3/4×ATR stops, no regime cap) | **+28.3%** | **−3.9%** |
| Default + regime filter | +17.9% | −7.2% |
| Slower turnover (5×ATR trail, 10-day min hold) | +16.8% | +17.8% |

**What the numbers say:**
- **Relative to NIFTY:** the default beat NIFTY in both periods.
- **Absolute return:** it lost money in the falling unseen period, with a −26% worst drawdown against NIFTY's −16%.
- **Single numbers are unreliable:** one full-period replay (Jan 2024 – Oct 2026) shows +54% vs NIFTY +2%. Yet the two halves run separately compound to only about +23%, because which stocks fill the six slots changes outcomes a lot.
- **Expectation for your six months:** better than NIFTY in some months and worse in others, and deeper falls than the index. Six months may well be too short to tell skill from luck.
- **Charges:** with ₹50k on Groww, a round trip costs about 0.66% (mostly the fixed DP fee and STT). Zerodha's free delivery brokerage cut charges by about 37% in tests.

## Troubleshooting

- **`error while attempting to bind on address ... 8000`:** another program, or a second QuantDesk, holds the port. `qd start` now detects a running QuantDesk and reuses it, or picks the next free port.
- **Second autopilot:** only one autopilot can run per machine; an OS lock refuses a second one, because two would double every order.
- **After updating the code:** restart `qd start` (Ctrl+C, then run it again). Running processes keep the code they started with.

## Moving to Groww (after the evaluation)

1. **API access:** subscribe to the Groww Trading API. Put `GROWW_API_KEY`/`GROWW_API_SECRET` (or the TOTP pair) in `.env`.
2. **Static IP:** whitelist this machine's static IP with Groww (SEBI requirement).
3. **Install and check the connection:**
   ```powershell
   pip install -e ".[groww]"
   qd broker login groww
   qd broker test groww        # read-only
   ```
4. **Configure `config/settings.local.yaml`:**
   ```yaml
   execution: {mode: live, broker: groww}
   agent: {initial_capital: 50000}
   ```
5. **Unlock live orders:** add `QD_LIVE_CONFIRM=I_UNDERSTAND_THE_RISK` to `.env`.

Groww sessions expire every morning. The TOTP login is fully scripted; the key + secret flow needs a daily approval in Groww's console.

## Honest limitations

* **Delayed prices:** free Yahoo prices can lag the exchange by a few minutes, so paper fills use slightly stale prints. Live mode uses Groww's own feed.
* **Backtest gaps:** the backtest can't replay ML, forecasts, news or Claude, and today's stock list omits companies that delisted. Treat it as a sanity check, not a promise.
* **Six months is short:** a real edge and luck are hard to tell apart in that time. The Evaluation page reports an alpha t-stat for that reason.
* **Sector labels mix taxonomies:** NSE index classifications where available, Yahoo's otherwise.

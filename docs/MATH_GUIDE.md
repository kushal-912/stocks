# The maths behind QuantDesk

A field guide to every number the app shows, where it lives in the code, and how it fails. Read it in order - each layer builds on the previous one.

---

## 0. The uncomfortable truth first

Daily stock returns are *close to* unpredictable. Typical out-of-sample directional accuracy for good models on liquid stocks is **51-56%**. Professional quant funds make money from (a) many small, weakly-predictive signals combined, (b) strict risk and cost control, and (c) diversification across many bets - not from a single brilliant forecast. QuantDesk is built around that idea: every model votes, risk management decides the size, and the playground shows you what the *costs* do to a strategy.

---

## 1. Returns and risk - `quantdesk/analytics/risk.py`

**Simple vs log return.** $r_t = P_t/P_{t-1}-1$; $\ell_t = \ln(P_t/P_{t-1})$. Log returns add over time, simple returns add across assets in a portfolio.

**Annualised volatility** $\sigma = \text{std}(r)\sqrt{252}$ - the square-root-of-time rule assumes independent returns (only roughly true).

**CAGR** $= (V_T/V_0)^{1/\text{years}}-1$.

**Sharpe ratio** $= \dfrac{\bar r - r_f}{\sigma_r}\sqrt{252}$. Excess return per unit of total risk. >1 sustained out-of-sample is excellent. **Sortino** uses only downside deviation. **Calmar** = CAGR / |max drawdown|.

**Max drawdown** - the worst peak-to-trough fall of the equity curve: $\min_t (V_t/\max_{s\le t}V_s - 1)$. This, not volatility, is what makes people abandon strategies.

**Value-at-Risk (95%)** - the loss exceeded on only 5% of days. Three estimators are implemented:
* historical (empirical 5th percentile),
* parametric $-(\mu + z_{0.05}\sigma)$ (assumes normality - underestimates tails),
* **Cornish-Fisher**, which corrects $z$ for skew $S$ and excess kurtosis $K$:
  $z_{cf} = z + (z^2-1)\frac{S}{6} + (z^3-3z)\frac{K}{24} - (2z^3-5z)\frac{S^2}{36}$.

**CVaR / Expected Shortfall** - the *average* loss on days worse than VaR. Better behaved than VaR (it is coherent/sub-additive).

**Beta & alpha (CAPM)** - regress stock returns on NIFTY returns: $r_i = \alpha + \beta r_m + \epsilon$. $\beta=1.3$ means the stock moves ~1.3% per 1% market move.

---

## 2. Technical indicators - `quantdesk/analytics/indicators.py`

All are *transformations of past prices*, so they cannot know the future - but they summarise trend, momentum and stretch compactly.

| Indicator | Formula (sketch) | What it measures |
|---|---|---|
| SMA / EMA | mean of last n / exponentially weighted ($\alpha = 2/(n+1)$) | trend level |
| RSI | $100 - \frac{100}{1+RS}$, $RS$ = Wilder-avg gain / avg loss | stretch, 0-100 |
| MACD | EMA12 − EMA26, signal = EMA9 of that | momentum impulse |
| Bollinger | SMA20 ± 2σ; %B = position within the band | stretch vs recent vol |
| ATR | Wilder avg of true range $\max(H-L, |H-C_{-1}|, |L-C_{-1}|)$ | volatility in ₹ - used for stops |
| ADX | smoothed $|{+DI}-{-DI}|/({+DI}+{-DI})$ | trend *strength*, not direction |
| Supertrend | ATR trailing band that flips on closes | trend state |
| VWAP | $\sum p_i v_i / \sum v_i$ within the session | intraday "fair" price |

**Look-ahead bias** is the #1 backtest bug: using the close of day *t* to trade at the close of day *t*. The backtester always executes at the *next* open; `tests/test_backtest.py::test_no_lookahead_execution` proves a cheating strategy can't profit.

---

## 3. Is this series trending or mean-reverting? - `quantdesk/analytics/stats.py`

* **Hurst exponent** $H$: the spread of $k$-day log-price changes grows like $k^H$. $H\approx0.5$ random walk, $>0.5$ trending (persistent), $<0.5$ mean-reverting.
* **ADF test** (Augmented Dickey-Fuller): null = unit root (non-stationary). Prices almost never reject; returns always do.
* **KPSS**: the opposite null (stationary). Use both: ADF rejects + KPSS doesn't ⇒ stationary.
* **Variance ratio** (Lo-MacKinlay): $VR(k) = \text{Var}(r_t^{(k)})/(k\,\text{Var}(r_t))$. Random walk ⇒ 1.
* **Ornstein-Uhlenbeck half-life**: fit $\Delta y_t = a + b\,y_{t-1}$; half-life $= -\ln 2 / b$. Tells you how long a mean-reversion trade should take.
* **Ljung-Box**: are the first *k* return autocorrelations jointly zero?
* **Jarque-Bera**: are returns normal? (They aren't - fat tails. This is why VaR needs care.)
* **Cointegration (Engle-Granger)**: two non-stationary prices whose spread $\ln A - \beta \ln B$ *is* stationary. Basis of pairs trading.

---

## 4. Forecasting - `quantdesk/models/timeseries.py`

* **ARIMA(p,1,q)** on log price = ARMA(p,q) on log returns: $r_t = c + \sum\phi_i r_{t-i} + \sum\theta_j\epsilon_{t-j} + \epsilon_t$. Order chosen by AIC (fit vs complexity).
* **ETS / Holt damped trend**: level + trend updated by exponential smoothing; the damping factor stops the trend extrapolating forever.
* **Kalman filter (local linear trend)**: hidden state = [level, slope]. Each day it *predicts* the state, observes the price, and *corrects* by the Kalman gain $K = P H^T (HPH^T+R)^{-1}$. The filtered slope and its uncertainty give a noise-robust trend t-stat.
* **GARCH(1,1)** for volatility: $\sigma_t^2 = \omega + \alpha\epsilon_{t-1}^2 + \beta\sigma_{t-1}^2$. Volatility clusters, and $\alpha+\beta$ (persistence) says how long shocks last. Unlike returns, **volatility is genuinely forecastable** - that's why it drives position sizing.
* **Forecast combination**: an inverse-variance weighted average of the models. A classic empirical result: simple combinations beat the best individual model out of sample.

Every forecast reports `t_stat = expected / std`. Values near 0 mean "no information" - be honest about it.

---

## 5. Machine learning - `quantdesk/models/ml.py`

Features (`feature_frame`): multi-horizon returns, realised vols, RSI, MACD, Bollinger, ATR%, distance from SMAs, ADX, stochastic, volume z-score, OBV slope.
Label: sign of the next 5-day return. Model: gradient-boosted trees (also RF / logistic).

**Purged walk-forward validation** - the only honest way to test time-series ML:
1. train on data up to *t − h* (the *h*-bar gap = "purge", because 5-day labels overlap),
2. predict the next 63 bars, 3. roll forward and repeat.

Report: accuracy vs the **naive base rate** (always predicting "up"), **AUC** (0.5 = coin flip), **Brier score** (probability calibration). The composite automatically zeroes the ML vote if AUC ≤ 0.5.

Never use random K-fold on time series - it leaks the future into training.

---

## 6. News & LLMs - `quantdesk/nlp/`

* **Lexicon** (offline): finance-specific word and phrase lists in the spirit of Loughran-McDonald, with negation handling. General-purpose sentiment lexicons misread finance text.
* **Claude**: each headline is classified (structured JSON) into sentiment ∈ [−1,1], relevance, impact and event type. Results are cached per headline.
* **Aggregation**: $S = \frac{\sum w_i s_i}{\sum w_i}$, $w_i = 0.5^{\text{age}_i/\text{half-life}}\cdot\text{relevance}_i\cdot\text{impact}_i$. Confidence grows with $\sum w_i$.

Caveats: headlines are often priced in within minutes; sentiment can't be backtested without a historical news archive; LLM output is one *vote*, never an order trigger by itself.

---

## 7. The composite signal - `quantdesk/strategies/composite.py`

Each view is squashed to [−1, 1] (often with $\tanh$) and averaged with weights from `config/settings.yaml`:

$$\text{score} = \frac{\sum_k w_k c_k}{\sum_{k\,\text{present}} w_k}$$

* **trend** = (sign(P − SMA200) + sign(SMA50 − SMA200) + tanh(DI spread/20))/3, scaled up when ADX is high
* **momentum** = 0.7·tanh(12-1 month return / vol) + 0.3·tanh(MACD hist / ATR) - the 12-1 month effect is one of the most documented anomalies (Jegadeesh-Titman 1993; Moskowitz-Ooi-Pedersen 2012)
* **mean reversion** = contrarian RSI and %B, *damped when ADX says the trend is strong* (don't short a freight train)
* **forecast**, **ml**, **sentiment** as above

Why combine? If $N$ signals each have information coefficient IC and are uncorrelated, the combined IC grows roughly like $\sqrt N$ (Grinold's "fundamental law of active management": $IR \approx IC\sqrt{\text{breadth}}$).

---

## 8. Position sizing - `quantdesk/risk/sizing.py`

Quantity = the **minimum** of:
1. **Fixed-fractional risk**: $q = \dfrac{\text{capital}\times 1\%}{\text{entry} - \text{stop}}$, stop = entry − 2·ATR. A stop-out costs ~1% of capital.
2. **Concentration**: position ≤ 20% of equity.
3. **Volatility targeting**: exposure × min(1, σ_target/σ_forecast).
4. **Fractional Kelly**: Kelly fraction $f^* = p - \frac{1-p}{b}$ ($p$ win prob, $b$ reward/risk). Full Kelly maximises long-run growth but has brutal drawdowns and assumes you know $p$ exactly - you don't - so use ¼ Kelly.

Position sizing matters more than entry signals for long-run survival.

---

## 9. Portfolio construction - `quantdesk/analytics/portfolio.py`

* **Markowitz mean-variance**: maximise $\frac{w^T\mu - r_f}{\sqrt{w^T\Sigma w}}$ s.t. $\sum w = 1$, $w\ge0$. Highly sensitive to errors in $\mu$ ("error maximisation").
* **Ledoit-Wolf shrinkage**: $\hat\Sigma = \delta F + (1-\delta) S$ - shrink the noisy sample covariance toward a structured target.
* **Minimum variance**: ignore $\mu$ entirely; surprisingly competitive.
* **Risk parity (ERC)**: each asset's risk contribution $w_i(\Sigma w)_i/\sigma_p^2$ is equal.
* **HRP** (López de Prado 2016): cluster assets by correlation distance $\sqrt{(1-\rho)/2}$, order them, then split risk recursively by inverse variance. No matrix inversion ⇒ stable.

---

## 10. Options - `quantdesk/analytics/options.py`

Black-Scholes call: $C = S e^{-qT}N(d_1) - K e^{-rT}N(d_2)$, $d_{1,2} = \frac{\ln(S/K) + (r - q \pm \sigma^2/2)T}{\sigma\sqrt T}$.
Greeks: Δ (∂C/∂S), Γ, vega, θ, ρ. Implied volatility = the σ that reproduces the market price (solved with Brent's method). CRR binomial trees handle American early exercise. Put-call parity $C - P = S - Ke^{-rT}$ is a test in `tests/test_analytics.py`.

---

## 11. Simulation - `quantdesk/analytics/simulation.py`

* **GBM**: $S_{t+\Delta} = S_t \exp\big((\mu - \tfrac12\sigma^2)\Delta + \sigma\sqrt\Delta Z\big)$. The $-\tfrac12\sigma^2$ term is why volatility drags compound growth.
* **Block bootstrap**: resample 10-day blocks of real returns - keeps fat tails and volatility clustering that GBM ignores.
* Read the cone, not the median line: the 5th-percentile outcome is what you must be able to live with.

---

## 12. Backtesting honestly - `quantdesk/backtest/`

* Next-bar execution, whole shares, no negative cash.
* **Costs** (Indian delivery, approx.): STT 0.1% each side, exchange 0.00297%, SEBI ₹10/crore, stamp 0.015% on buys, GST 18% on brokerage+fees, DP ~₹15-17 per sell. Round-trip ≈ **0.25%+**. A strategy trading 50 times a year pays ~12.5%/yr in costs before it earns anything.
* **Overfitting**: try 100 parameter sets and the best will look great by luck. Use the Parameter Lab: optimise in-sample, judge out-of-sample.
* **Survivorship bias**: today's watchlist contains companies that *survived*. Backtests on them are optimistic.
* Tax: STCG 20% (≤12 months), LTCG 12.5% above ₹1.25 lakh/yr (post July-2024 rates). Intraday profit is business income.

---

## 13. Rules of the road (India)

* **SEBI retail algo framework** (mandatory from 1 Apr 2026): self-built algos for your own/family accounts are fine via broker APIs; ≥10 orders/sec per exchange requires strategy registration (QuantDesk caps at 9 and defaults to 5); static IP whitelisting; daily 2FA login; orders tagged with the broker's algo ID. Selling/sharing your algo to others requires registration (Research Analyst + exchange empanelment).
* No naked short selling in delivery; intraday (MIS) positions are squared off by the broker before close.
* Keep records - QuantDesk writes an append-only audit log at `data/audit/audit.jsonl`.

Always check the latest circulars on sebi.gov.in and your broker's notices - rules change.

---

## How the agent puts it together

1. **Screen the whole market** (`quantdesk/trader/screener.py`): cross-sectional z-scores of momentum, 52-week-high proximity, risk-adjusted momentum and low volatility, after liquidity, trend and over-extension gates. Cross-sectional ranking is more robust than absolute forecasts - you only need to know which stocks are *relatively* strong.
2. **Deep research** on the top 25 (sections 3-6): forecasts, GARCH, walk-forward ML, news.
3. **Final conviction** = 0.4 × screen score + 0.6 × deep composite (section 7).
4. **Claude review** can veto a buy for context the models can't see; it can never add one.
5. **Portfolio policy** (`quantdesk/trader/policy.py`): sizing (section 8), sector caps, regime filter, swap margin to control turnover.
6. **Execution & risk**: limit entries with a gap filter, resting SL-M stops, ATR trailing stops, hard limits on every order.

## Suggested learning path

1. Research page each morning - read *why* each stock ranked where it did.
2. Stock page for a holding - compare the model votes with what the price did next.
3. Backtest page - change `agent:` settings in `config/settings.local.yaml` and re-run; notice how small changes move results (and how easy overfitting is).
4. Evaluation page monthly - judge excess return, drawdown and the alpha t-stat, not raw profit.

**Books**: *Advances in Financial Machine Learning* (López de Prado), *Quantitative Trading* & *Algorithmic Trading* (Ernest Chan), *Active Portfolio Management* (Grinold & Kahn), *Options, Futures and Other Derivatives* (Hull), *Analysis of Financial Time Series* (Tsay).

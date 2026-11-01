"""QuantDesk - a personal quantitative trading workbench.

Layers (bottom -> top):
    data        market prices, news, symbol mapping, calendar
    analytics   indicators, risk metrics, statistical tests, portfolio maths, options, simulation
    models      time-series forecasts (ARIMA, ETS, Kalman, GARCH), ML classifiers, regimes
    nlp         lexicon + LLM (Claude) news sentiment, daily brief
    strategies  rule-based and composite signal generators
    backtest    event-driven simulator with Indian transaction costs
    risk        position sizing, risk guard, SEBI-aware compliance, audit log
    broker      paper / Groww / Zerodha adapters behind one interface
    engine      daily pre-market pipeline and continuous intraday monitor
    playground  "what if I had invested" time machine on real prices
"""

__version__ = "0.1.0"

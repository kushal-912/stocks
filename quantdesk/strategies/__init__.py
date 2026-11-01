from quantdesk.strategies.base import Strategy
from quantdesk.strategies.composite import CompositeStrategy
from quantdesk.strategies.library import (
    BollingerReversion, BuyAndHold, DonchianBreakout, EMACrossover, MACDMomentum, MLStrategy,
    RSIMeanReversion, SMACrossover, SupertrendFollow, TimeSeriesMomentum,
)

REGISTRY: dict[str, type[Strategy]] = {
    cls.name: cls for cls in (
        BuyAndHold, SMACrossover, EMACrossover, RSIMeanReversion, BollingerReversion, DonchianBreakout,
        MACDMomentum, TimeSeriesMomentum, SupertrendFollow, CompositeStrategy, MLStrategy,
    )
}


def make_strategy(name: str, **params) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"Unknown strategy '{name}'. Available: {', '.join(REGISTRY)}")
    return REGISTRY[name](**params)


__all__ = ["Strategy", "REGISTRY", "make_strategy"]

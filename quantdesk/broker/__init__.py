from quantdesk.broker.base import Broker, BrokerError, Funds, OrderRequest, OrderResult, Position
from quantdesk.broker.paper import PaperBroker


def get_broker(name: str | None = None, connect: bool = True) -> Broker:
    """Broker named in settings (execution.broker) unless overridden. Live brokers are only used when
    execution.mode == 'live'; otherwise the paper broker is returned."""
    from quantdesk.config import get_settings

    s = get_settings()
    name = name or (s.execution.broker if s.execution.mode == "live" else "paper")
    if name == "paper":
        return PaperBroker()
    if name == "zerodha":
        from quantdesk.broker.zerodha import ZerodhaBroker

        b = ZerodhaBroker()
    elif name == "groww":
        from quantdesk.broker.groww import GrowwBroker

        b = GrowwBroker()
    else:
        raise BrokerError(f"Unknown broker '{name}'")
    if connect:
        b.connect()
    return b


__all__ = ["Broker", "BrokerError", "Funds", "OrderRequest", "OrderResult", "Position", "PaperBroker", "get_broker"]

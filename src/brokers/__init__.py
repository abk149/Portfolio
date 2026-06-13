"""Broker abstraction — either/or active broker (Upstox or Groww).

The active broker is chosen by settings.broker (env BROKER). Both client
classes expose the SAME method surface and return the SAME shapes, so every
downstream consumer (MarketData, PortfolioManager, screener, optimizer, …)
works unchanged regardless of which broker is active.

    from src.brokers import get_broker
    client = get_broker()          # → UpstoxClient or GrowwClient
"""
from .base import BrokerAuthError  # noqa: F401
from .factory import broker_status, get_broker  # noqa: F401

"""Shared broker types.

`BrokerAuthError` is the generic "broker not authenticated" error. The existing
`UpstoxClient.UpstoxAuthError` is registered as a subclass so any code that
already does `except UpstoxAuthError` keeps working, and new code can catch the
broader `BrokerAuthError` to cover every broker.
"""
from __future__ import annotations


class BrokerAuthError(RuntimeError):
    """Raised when the active broker has no valid token / credentials."""
    pass


# The method surface every broker client must provide (duck-typed; we keep it
# as documentation rather than an enforced ABC so the existing UpstoxClient —
# which predates this package — satisfies it without modification):
#
#   profile() -> dict
#   funds() -> dict
#   holdings() -> list[dict]        # each: tradingsymbol, quantity,
#                                   #       average_price, last_price, pnl,
#                                   #       day_change, instrument_token/key
#   positions() -> list[dict]
#   order_book() -> list[dict]
#   trades_today() -> list[dict]
#   trade_history(start, end, segment="EQ") -> list[dict]
#   ltp(instruments: list[str]) -> dict
#   quote(instruments: list[str]) -> dict
#   candles(instrument_key, interval="day", to_date=None, from_date=None) -> DataFrame
#   intraday_candles(instrument_key, interval="30minute") -> DataFrame
BROKER_SURFACE = (
    "profile", "funds", "holdings", "positions", "order_book", "trades_today",
    "trade_history", "ltp", "quote", "candles", "intraday_candles",
)

"""Lazy re-exports — keep module import cheap.

`from src.data import MarketData` no longer downloads anything. The instrument
master is only fetched the first time you actually call `resolve_universe()`
or `resolve_instrument_key()`, and is then cached for 24 h on disk.
"""
from .market_data import MarketData  # noqa: F401
from .universe import UNIVERSES  # noqa: F401


def __getattr__(name):
    # Lazy-load the heavyweight `instruments` module on first reference.
    if name in {"equities", "load_instruments", "resolve_instrument_key",
                "resolve_universe", "search"}:
        from . import instruments as _i
        return getattr(_i, name)
    raise AttributeError(name)

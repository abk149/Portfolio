"""Shared fixtures: a fully stubbed backend, so tests need no broker or network.

Every external dependency the API touches — broker, market data, LLM, KB — is
replaced with a deterministic fake that deliberately returns AWKWARD data:
NaN, ±Inf, numpy scalars, tz-aware indices mixed with tz-naive ones. Those are
the shapes that have actually broken this API in production, so the smoke test
exercises them on every route rather than hoping for a clean day.
"""
import numpy as np
import pandas as pd
import pytest


# --- price frames in the two shapes the two sources really produce ----------
def broker_frame(n=260, base=100.0):
    """Broker shape: tz-AWARE index (Upstox sends +05:30)."""
    idx = pd.date_range("2025-01-01", periods=n, freq="B", tz="Asia/Kolkata")
    close = base + np.cumsum(np.random.default_rng(1).normal(0, 1, n))
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                         "close": close, "volume": 1000, "oi": 0}, index=idx)


def yahoo_frame(n=260, base=50.0):
    """Yahoo shape: tz-NAIVE index built from epoch seconds."""
    epoch = pd.Timestamp("1970-01-01")
    secs = [int((d - epoch).total_seconds())
            for d in pd.date_range("2025-01-01", periods=n, freq="B")]
    close = base + np.cumsum(np.random.default_rng(2).normal(0, 0.5, n))
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                         "close": close, "volume": 900, "oi": 0},
                        index=pd.to_datetime(secs, unit="s"))


HOLDINGS = pd.DataFrame({
    "tradingsymbol": ["AAA", "BBB", "CCC"],
    "instrument_key": ["NSE_EQ|A", "NSE_EQ|B", "NSE_EQ|C"],
    "quantity": np.array([10, 20, 30], dtype="int64"),
    "average_price": [100.0, 200.0, 50.0],
    "last_price": [110.0, 190.0, np.nan],        # a missing quote
    "current_value": [1100.0, 3800.0, 1500.0],
    "pnl": [100.0, -200.0, np.inf],              # a degenerate P&L
    "pnl_pct": [10.0, -5.0, np.nan],
    "sector": ["IT", "Bank", None],
})


class FakeBroker:
    """Alternates the two index shapes, so every join sees a mixed set."""
    _n = 0

    def holdings(self):
        return HOLDINGS.to_dict("records")

    def positions(self):
        return []

    def candles(self, ikey, interval="day", from_date=None, to_date=None):
        FakeBroker._n += 1
        return broker_frame() if FakeBroker._n % 2 else yahoo_frame()

    def intraday_candles(self, ikey, interval="30minute"):
        return broker_frame(n=60)

    def ltp(self, keys):
        return {k: {"last_price": 123.45} for k in keys}

    def quote(self, keys):
        return {k: {"last_price": 123.45, "ohlc": {"open": 1, "high": 2, "low": 1,
                                                   "close": 1}} for k in keys}

    def _get(self, path, params=None):
        """Raw REST passthrough — PerformanceAnalyzer calls this directly for
        /charges/historical-trades."""
        if "historical-trades" in path:
            if (params or {}).get("page_number", 1) > 1:
                return []
            return self.trade_history(None, None)
        return {}

    def trade_history(self, start, end):
        return [
            {"trade_date": "2025-03-03", "order_timestamp": "2025-03-03T10:00",
             "quantity": 10, "price": 100.0, "transaction_type": "BUY",
             "tradingsymbol": "AAA", "instrument_token": "NSE_EQ|A",
             "trade_value": 1000.0, "trade_id": "t1"},
            {"trade_date": "2025-03-03", "order_timestamp": "2025-03-03T15:00",
             "quantity": 10, "price": 106.0, "transaction_type": "SELL",
             "tradingsymbol": "AAA", "instrument_token": "NSE_EQ|A",
             "trade_value": 1060.0, "trade_id": "t2"},
        ]


class FakeLLM:
    name = "fake"

    def complete(self, system, user):
        return "## Section\nA grounded answer.\n\n- point one\n- point two"

    def tool_loop(self, *a, **k):
        return "ok"


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """A TestClient over the real app with every external edge stubbed."""
    import importlib
    import sys
    A = importlib.import_module("src.dashboard.app")
    A = sys.modules["src.dashboard.app"]

    fake = FakeBroker()
    get_broker = lambda *a, **k: fake                      # noqa: E731
    get_llm = lambda *a, **k: FakeLLM()                    # noqa: E731

    # These names are bound at IMPORT time in each consumer, so patching the
    # source module alone does nothing — the consumer already holds its own
    # reference. Patch every binding, or the tests quietly hit the real broker
    # and the real LLM (which is exactly what happened the first time).
    for mod in ("src.brokers", "src.data.market_data", "src.portfolio.manager",
                "src.portfolio.optimizer", "src.portfolio.analytics",
                "src.intraday.scanner", "src.intraday.analyzer",
                "src.screener.engine", "src.screener.talib_screener",
                "src.tools.quant_calculator", "src.universe_map.builder"):
        m = importlib.import_module(mod)
        if hasattr(m, "get_broker"):
            monkeypatch.setattr(m, "get_broker", get_broker)
    for mod in ("src.llm", "src.llm.factory", "src.agents.base",
                "src.agents.quant_agent"):
        m = importlib.import_module(mod)
        if hasattr(m, "get_llm"):
            monkeypatch.setattr(m, "get_llm", get_llm)

    import src.portfolio.ghost as ghost
    monkeypatch.setattr(ghost, "_store_path", lambda: tmp_path / "ghost.json")

    # Keep the suite offline and fast: no news crawl, no Reddit, no calendar
    # scraping. Those have their own tests.
    import src.tools.news_sources as news
    monkeypatch.setattr(news, "fetch_all", lambda **k: {
        "items": [], "health": {"sources": [], "total": 0, "healthy": 0,
                                "degraded": []},
        "counts": {"items": 0}, "as_of": "2026-01-01T00:00"})
    import src.tools.market_calendar as cal
    monkeypatch.setattr(cal, "fetch_fomc", lambda a, b: [])
    monkeypatch.setattr(cal, "fetch_rbi", lambda a, b: ([], []))
    monkeypatch.setattr(cal, "bulletin", lambda **k: [])

    # Fundamentals are live scrapers — 30s of network per screener run, and a
    # different answer every day. Return a fixed row that deliberately contains
    # a missing metric, since that is the shape that broke JSON encoding.
    def fake_fundamentals(symbol):
        return {"_sources": ["stub"], "pe": 18.5, "roe_pct": 21.0,
                "debt_to_equity": 0.35, "sales_growth_pct": 9.0,
                "profit_growth_pct": None,          # the missing-metric case
                "market_cap_cr": 50000.0, "sector": "IT"}
    import src.tools.screener_in as screener_in
    monkeypatch.setattr(screener_in, "fetch_fundamentals", fake_fundamentals)
    import src.screener.fundamental as fnd
    if hasattr(fnd, "fetch_fundamentals"):
        monkeypatch.setattr(fnd, "fetch_fundamentals", fake_fundamentals)
    import src.screener.engine as eng
    if hasattr(eng, "fetch_fundamentals"):
        monkeypatch.setattr(eng, "fetch_fundamentals", fake_fundamentals)

    # Macro snapshot hits NSE + Yahoo; pin it.
    import src.tools.macro as macro
    monkeypatch.setattr(macro.MacroSnapshot, "market_mode",
                        lambda self, m=None: {"mode": "NEUTRAL", "regime": "NEUTRAL",
                                              "reasons": [], "notes": [],
                                              "india_vix": 12.0, "nifty_pcr": None,
                                              "usdinr": 94.0, "nifty_change_pct": 0.1})

    from fastapi.testclient import TestClient
    return TestClient(A.app, raise_server_exceptions=False), A

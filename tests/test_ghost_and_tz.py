"""Regression tests for the ghost portfolio and the mixed-timezone price join.

The timezone bug: the broker returns ISO timestamps with +05:30 (tz-AWARE) and
the Yahoo fallback returns epoch seconds (tz-NAIVE). As soon as one ticker fell
back — the normal case — the cash-allocation optimiser's concat raised
"Cannot join tz-naive with tz-aware DatetimeIndex".
"""
import numpy as np
import pandas as pd
import pytest

from src.data.market_data import _normalise_daily_index
from src.portfolio.optimizer import PortfolioOptimizer


def _broker_df(n=250, base=100.0):
    """Broker shape: tz-aware index."""
    idx = pd.date_range("2025-09-08", periods=n, freq="B", tz="Asia/Kolkata")
    return pd.DataFrame({"close": base + np.arange(n) * 0.1}, index=idx)


def _yahoo_df(n=250, base=50.0):
    """Yahoo shape: tz-naive index built from epoch seconds, as yahoo.daily does.

    Computed explicitly rather than via `astype("int64")` — that returns
    nanoseconds on pandas 2.x and the index's own unit on 3.x, so the shortcut
    silently produced 1970 dates on one of the two versions this runs on.
    """
    epoch = pd.Timestamp("1970-01-01")
    secs = [int((d - epoch).total_seconds())
            for d in pd.date_range("2025-09-08", periods=n, freq="B")]
    return pd.DataFrame({"close": base + np.arange(n) * 0.05},
                        index=pd.to_datetime(secs, unit="s"))


def test_normalise_strips_timezone():
    assert _broker_df().index.tz is not None
    assert _normalise_daily_index(_broker_df()).index.tz is None
    assert _normalise_daily_index(_yahoo_df()).index.tz is None


def test_mixed_sources_can_be_joined():
    """The exact operation that raised the error."""
    a = _normalise_daily_index(_broker_df())["close"]
    b = _normalise_daily_index(_yahoo_df())["close"]
    joined = pd.concat({"A.NS": a, "B.NS": b}, axis=1).dropna(how="any")
    assert len(joined) > 200 and list(joined.columns) == ["A.NS", "B.NS"]


def test_returns_survives_mixed_sources():
    """End to end: the optimiser path that cash allocation uses."""
    class MD:
        def daily(self, yf_t, ikey, lookback_days=365):
            # Half the tickers come from the broker, half from the fallback.
            return _broker_df() if yf_t in ("A.NS", "B.NS") else _yahoo_df()
    opt = PortfolioOptimizer(); opt.md = MD()
    rets = opt.returns([(t, None) for t in ("A.NS", "B.NS", "C.NS", "D.NS")])
    assert not rets.empty and rets.shape[1] == 4


def test_deploy_cash_completes_with_mixed_sources():
    class MD:
        def daily(self, yf_t, ikey, lookback_days=365):
            return _broker_df() if yf_t in ("A.NS", "B.NS") else _yahoo_df()
    opt = PortfolioOptimizer(); opt.md = MD()
    res = opt.deploy_cash({"A.NS": 1e5, "B.NS": 8e4, "C.NS": 6e4}, 50_000.0, ["D.NS"], 0.4)
    assert "error" not in res, res.get("error")
    assert res["buys"] and all(isinstance(b["is_new_position"], bool) for b in res["buys"])


# ---------------------------------------------------------------------------
# Ghost portfolio
# ---------------------------------------------------------------------------
@pytest.fixture()
def store(tmp_path, monkeypatch):
    from src.portfolio import ghost
    monkeypatch.setattr(ghost, "_store_path", lambda: tmp_path / "ghost.json")
    monkeypatch.setattr(ghost, "_price", lambda sym: {"AAA": 100.0, "BBB": 250.0}.get(sym))
    return ghost


def test_buy_books_at_the_live_price(store):
    r = store.buy("AAA", amount=10_000)
    p = r["position"]
    assert p["qty"] == 100 and p["entry_price"] == 100.0 and p["invested"] == 10_000.0


def test_buy_refuses_when_it_cannot_price(store):
    """A paper record with an invented entry is worthless as a test."""
    assert "error" in store.buy("UNPRICEABLE", amount=10_000)


def test_buy_refuses_amount_below_one_share(store):
    assert "error" in store.buy("BBB", amount=100)


def test_snapshot_marks_to_market(store, monkeypatch):
    store.buy("AAA", amount=10_000)
    monkeypatch.setattr(store, "_price", lambda sym: 110.0)
    s = store.snapshot()
    assert s["summary"]["unrealised_pnl"] == 1000.0
    assert s["summary"]["unrealised_pnl_pct"] == 10.0


def test_closed_positions_keep_their_realised_loss(store, monkeypatch):
    """The track record must include the ones that went wrong."""
    pid = store.buy("AAA", amount=10_000)["position"]["id"]
    monkeypatch.setattr(store, "_price", lambda sym: 90.0)
    store.sell(pid)
    s = store.snapshot()
    assert s["summary"]["realised_pnl"] == -1000.0
    assert s["summary"]["total_pnl"] == -1000.0
    assert s["summary"]["n_open"] == 0 and s["summary"]["n_closed"] == 1


def test_sell_is_idempotent(store):
    pid = store.buy("AAA", amount=10_000)["position"]["id"]
    assert store.sell(pid).get("ok")
    assert "error" in store.sell(pid)


def test_positions_survive_a_reload(store):
    store.buy("AAA", amount=10_000)
    assert store.snapshot()["summary"]["n_open"] == 1      # re-read from disk


def test_reset_clears_everything(store):
    store.buy("AAA", amount=10_000)
    store.reset()
    assert store.snapshot()["summary"]["n_open"] == 0

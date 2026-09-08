"""The wiring: does running an engine actually put picks in the queue?

These exist because the first version shipped with the store working perfectly
and the wiring broken. Every test was on `record()` directly, so nothing caught
that DR-Quant's capture was hooked only to the DESKTOP subprocess branch — on
the phone, which runs it in a thread, picks never reached the queue at all.

So: drive the real endpoints and assert the queue fills.
"""
import importlib
import sys
import time

import pytest


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    from src.portfolio import ghost, recommendations as rec
    monkeypatch.setattr(rec, "_store_path", lambda: tmp_path / "rec.json")
    monkeypatch.setattr(ghost, "_store_path", lambda: tmp_path / "ghost.json")
    monkeypatch.setattr(ghost, "_price", lambda s: 100.0)
    A = sys.modules.get("src.dashboard.app") or importlib.import_module("src.dashboard.app")
    return A, rec


def _wait(A, job_id, secs=15):
    for _ in range(secs * 10):
        j = A.JOBS.get(job_id, {})
        if j.get("status") != "running":
            return j
        time.sleep(0.1)
    return {}


def test_macro_ideas_run_fills_the_queue(wired, monkeypatch):
    A, rec = wired
    import src.tools.macro_intel as mi
    monkeypatch.setattr(mi, "build_macro_themes", lambda **k: {
        "as_of": "2026-09-08T10:00",
        "picks": [{"symbol": "RELIANCE", "sector": "Energy", "conviction": "HIGH",
                   "thesis": "crude tailwind", "entry": {"suggested_entry": 1280.0}}],
    })
    _wait(A, A.api_themes({"days": 7})["job_id"])
    items = rec.list_all()["items"]
    assert [i["symbol"] for i in items] == ["RELIANCE"]
    assert items[0]["source"] == "macro-ideas"
    assert items[0]["suggested_entry"] == 1280.0


def test_cash_allocation_fills_the_queue(wired, monkeypatch):
    A, rec = wired
    import src.portfolio.manager as mgr
    import src.portfolio.optimizer as opt
    import pandas as pd

    class Snap:
        holdings = pd.DataFrame([{"tradingsymbol": "AAA", "current_value": 1000.0}])
    monkeypatch.setattr(mgr.PortfolioManager, "snapshot", lambda self: Snap())
    monkeypatch.setattr(opt.PortfolioOptimizer, "deploy_cash",
                        lambda self, **k: {"buys": [
                            {"ticker": "HDFCBANK.NS", "buy_inr": 18000.0,
                             "final_weight_pct": 9.0, "is_new_position": True}]})
    A.api_portfolio_deploy_cash({"cash": 18000, "include_universe": False})
    items = rec.list_all()["items"]
    assert [i["symbol"] for i in items] == ["HDFCBANK"]        # .NS stripped
    assert items[0]["source"] == "optimizer"
    assert items[0]["suggested_amount"] == 18000.0             # engine's own size


def test_dr_quant_fills_the_queue_on_the_ANDROID_path(wired, monkeypatch):
    """The bug: on-device DR-Quant runs in a thread, not a subprocess, and the
    capture was wired only to the subprocess branch."""
    A, rec = wired
    monkeypatch.setenv("APP_FILES_DIR", "/tmp/fake-android")
    import src.scheduler.jobs as jobs
    monkeypatch.setattr(jobs, "job_full_funnel_sync", lambda universe="nifty50": {
        "run_id": "q1", "candidates": 20,
        "validated": [{"symbol": "TCS", "health_score": 78, "sector": "IT",
                       "thesis": "passed the funnel"}],
    })
    out = A.api_quant_run({"universe": "nifty50"})
    assert out.get("in_process") is True, "this test must exercise the phone path"
    _wait(A, out["job_id"])
    items = rec.list_all()["items"]
    assert [i["symbol"] for i in items] == ["TCS"]
    assert items[0]["source"] == "dr-quant"
    assert items[0]["conviction"] == "HIGH"


def test_dr_quant_fills_the_queue_on_the_DESKTOP_path(wired, tmp_path):
    """The subprocess branch must keep working too."""
    A, rec = wired
    import json

    result = tmp_path / "q.json"
    result.write_text(json.dumps({
        "run_id": "q2",
        "validated": [{"symbol": "INFY", "health_score": 55, "thesis": "ok"}],
    }))

    class Proc:
        def poll(self): return 0
    A._QUANT_JOBS["desk1"] = {"proc": Proc(), "result": result,
                              "log": tmp_path / "q.log"}
    try:
        out = A.api_job("desk1")
        assert out["status"] == "done"
        items = rec.list_all()["items"]
        assert [i["symbol"] for i in items] == ["INFY"]
        assert items[0]["conviction"] == "MEDIUM"     # health_score < 70
    finally:
        A._QUANT_JOBS.pop("desk1", None)


def test_a_failed_capture_never_breaks_the_engine(wired, monkeypatch):
    """Recording is a side effect; it must not cost you the run's result."""
    A, rec = wired
    import src.portfolio.recommendations as r
    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(r, "record", boom)
    out = A._capture_quant({"validated": [{"symbol": "X"}]})
    assert out == {"validated": [{"symbol": "X"}]}

"""Hidden Gems → the ghost queue, driven through the real endpoints.

Same lesson as test_recommendation_capture: the store can work perfectly while
the wiring between an engine and the queue is broken, so these run a scan
through /api/gems/scan and then read the queue the way the Ghost tab does.
"""
import importlib
import sys
import time

import pytest

from tests.test_hidden_gems import GOOD_FUNDAMENTALS, _index_series, textbook_base


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    from src.portfolio import ghost, recommendations as rec
    import src.screener.hidden_gems as hg

    monkeypatch.setattr(rec, "_store_path", lambda: tmp_path / "rec.json")
    monkeypatch.setattr(ghost, "_store_path", lambda: tmp_path / "ghost.json")
    monkeypatch.setattr(ghost, "_price", lambda s: 106.0)

    # Two names in the universe, both shaped like a textbook long base.
    monkeypatch.setattr(hg, "resolve_universe", lambda u: [
        ("Alpha Ltd", "ALPHA.NS", "ALPHA", "NSE_EQ|ALPHA"),
        ("Beta Ltd", "BETA.NS", "BETA", "NSE_EQ|BETA"),
    ])

    def daily(self, ticker, ikey, lookback_days=None):
        if ikey is None:                       # the benchmark
            import pandas as pd
            s = _index_series()
            return pd.DataFrame({"close": s})
        return textbook_base()

    monkeypatch.setattr(hg.MarketData, "daily", daily)
    monkeypatch.setattr(hg.HiddenGemsEngine, "_fundamentals",
                        lambda self, sym: dict(GOOD_FUNDAMENTALS))

    A = sys.modules.get("src.dashboard.app") or importlib.import_module("src.dashboard.app")
    return A, rec


def _wait(A, job_id, secs=30):
    for _ in range(secs * 10):
        j = A.JOBS.get(job_id, {})
        if j.get("status") != "running":
            return j
        time.sleep(0.1)
    return {}


def test_a_gems_scan_fills_the_ghost_queue(wired):
    A, rec = wired
    job = _wait(A, A.api_gems_scan({"universe": "nifty50"})["job_id"])
    assert job.get("status") == "done", job
    gems = job["result"]["gems"]
    assert {g["symbol"] for g in gems} == {"ALPHA", "BETA"}

    items = A.api_recommendations()["items"]
    assert {i["symbol"] for i in items} == {"ALPHA", "BETA"}
    assert all(i["source"] == "hidden_gems" for i in items)
    assert all(i["source_label"] == "Hidden Gems" for i in items)
    assert all(i["status"] == "pending" for i in items)
    # The thesis travels with the pick, so the card isn't blank.
    assert all(i["rationale"] for i in items)


def test_gem_conviction_speaks_the_queue_language(wired):
    """The other engines say HIGH / MEDIUM / LOW; the queue colours and sorts on
    that. Raw gem verdicts (STRONG_GEM, WATCH…) would show as unknown labels."""
    A, rec = wired
    _wait(A, A.api_gems_scan({})["job_id"])
    convs = {i["conviction"] for i in A.api_recommendations()["items"]}
    assert convs <= {"HIGH", "MEDIUM", "LOW"}, convs


def test_rescanning_the_same_day_does_not_duplicate(wired):
    A, rec = wired
    _wait(A, A.api_gems_scan({})["job_id"])
    _wait(A, A.api_gems_scan({})["job_id"])
    syms = [i["symbol"] for i in A.api_recommendations()["items"]]
    assert sorted(syms) == ["ALPHA", "BETA"]


def test_a_gem_can_be_bought_into_the_ghost_portfolio(wired):
    A, rec = wired
    _wait(A, A.api_gems_scan({})["job_id"])
    item = next(i for i in A.api_recommendations()["items"] if i["symbol"] == "ALPHA")
    out = A.api_recommendation_take({"id": item["id"], "amount": 10_000})
    assert not out.get("error"), out
    snap = A.api_ghost()
    held = [p for p in snap["open"] if p["symbol"] == "ALPHA"]
    assert held and held[0]["source"] == "hidden_gems"
    # And the scorecard credits the right engine.
    by_src = {r["source"] for r in snap["attribution"]["by_source"]}
    assert "hidden_gems" in by_src


def test_a_stored_scan_can_be_queued_later_without_resurrecting_decisions(wired):
    """A scan stored before queueing existed can be queued on demand — but a
    pick you already dismissed stays dismissed."""
    A, rec = wired
    job = _wait(A, A.api_gems_scan({})["job_id"])
    assert job.get("status") == "done"

    # Pretend the queue never heard of this scan, except one decision the
    # user made: ALPHA dismissed.
    alpha = next(i for i in A.api_recommendations()["items"] if i["symbol"] == "ALPHA")
    A.api_recommendation_dismiss({"id": alpha["id"]})
    data = rec._load()
    data["items"] = [i for i in data["items"] if i["symbol"] != "BETA"]
    rec._save(data)

    out = A.api_gems_queue()
    assert out["ok"] and out["added"] == 1                # BETA back, ALPHA not
    by_sym = {i["symbol"]: i["status"] for i in rec._load()["items"]}
    assert by_sym == {"ALPHA": "dismissed", "BETA": "pending"}

    # Idempotent.
    assert A.api_gems_queue()["added"] == 0


def test_queueing_with_no_stored_scan_says_so(wired):
    A, rec = wired
    out = A.api_gems_queue()
    assert out["ok"] is False and "Run a scan" in out["error"]

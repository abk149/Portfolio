"""Durable engine results, and portfolio-aware sizing of the queue.

Two complaints, both about the queue not behaving like one system:
  * sizes were inconsistent — the cash optimiser reported a share of the book,
    Macro Ideas reported nothing so the UI showed a flat ₹25,000 default;
  * everything vanished on restart, because results lived only in the
    in-memory job table.
"""
import json
from datetime import datetime, timedelta

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Durable results
# ---------------------------------------------------------------------------
@pytest.fixture()
def store(tmp_path, monkeypatch):
    import src.data.results_store as rs
    monkeypatch.setattr(rs, "_dir", lambda: tmp_path)
    return rs


def test_a_result_survives_and_reports_its_age(store):
    store.save("themes", {"picks": [{"symbol": "AAA"}]})
    got = store.load("themes")
    assert got["payload"]["picks"][0]["symbol"] == "AAA"
    assert got["age_label"] == "just now"
    assert got["age_days"] < 0.01


def test_results_older_than_the_limit_are_deleted_not_shown(store, tmp_path):
    """A two-month-old 'current' macro view is worse than none — it looks fresh."""
    stale = {"kind": "themes",
             "saved_at": (datetime.now() - timedelta(days=61)).isoformat(),
             "payload": {"picks": []}}
    (tmp_path / "themes.json").write_text(json.dumps(stale))
    assert store.load("themes") is None
    assert not (tmp_path / "themes.json").exists()


def test_a_result_just_inside_the_limit_is_kept(store, tmp_path):
    ok = {"kind": "quant",
          "saved_at": (datetime.now() - timedelta(days=59)).isoformat(),
          "payload": {"validated": []}}
    (tmp_path / "quant.json").write_text(json.dumps(ok))
    got = store.load("quant")
    assert got is not None and got["age_label"] == "59 days ago"


def test_a_corrupt_result_file_is_discarded_not_raised(store, tmp_path):
    (tmp_path / "themes.json").write_text("not json")
    assert store.load("themes") is None


def test_purge_sweeps_everything_past_its_shelf_life(store, tmp_path):
    for kind, age in (("themes", 61), ("quant", 3)):
        (tmp_path / f"{kind}.json").write_text(json.dumps({
            "saved_at": (datetime.now() - timedelta(days=age)).isoformat(),
            "payload": {}}))
    assert store.purge()["removed"] == ["themes"]
    assert (tmp_path / "quant.json").exists()


def test_engines_persist_their_results(tmp_path, monkeypatch):
    """Running an engine must leave something behind for the next start."""
    import importlib, sys, time
    import src.data.results_store as rs
    import src.tools.macro_intel as mi
    monkeypatch.setattr(rs, "_dir", lambda: tmp_path)
    monkeypatch.setattr(mi, "build_macro_themes", lambda **k: {
        "as_of": "2026-09-08T10:00", "picks": [{"symbol": "AAA", "thesis": "t"}]})
    A = sys.modules.get("src.dashboard.app") or importlib.import_module("src.dashboard.app")
    jid = A.api_themes({"days": 7})["job_id"]
    for _ in range(150):
        if A.JOBS[jid]["status"] != "running":
            break
        time.sleep(0.1)
    assert rs.load("themes")["payload"]["picks"][0]["symbol"] == "AAA"


# ---------------------------------------------------------------------------
# Portfolio-aware sizing
# ---------------------------------------------------------------------------
@pytest.fixture()
def queue(tmp_path, monkeypatch):
    from src.portfolio import recommendations as rec
    monkeypatch.setattr(rec, "_store_path", lambda: tmp_path / "rec.json")
    return rec


def _size(A, rec, cash=50000):
    import time
    jid = A.api_recommendations_optimize({"cash": cash})["job_id"]
    for _ in range(150):
        if A.JOBS[jid]["status"] != "running":
            break
        time.sleep(0.1)
    return A.JOBS[jid]


def test_sizing_gives_every_source_the_same_currency(queue, monkeypatch):
    """The complaint: Macro Ideas showed a flat ₹25,000 while the optimiser
    showed a share of the book."""
    import importlib, sys
    import src.portfolio.manager as mgr
    import src.portfolio.optimizer as opt
    rec = queue
    A = sys.modules.get("src.dashboard.app") or importlib.import_module("src.dashboard.app")

    rec.record([{"symbol": "AAA"}], "macro-ideas", run_id="i1")
    rec.record([{"symbol": "BBB"}], "dr-quant", run_id="q1")
    assert all(i.get("suggested_amount") is None for i in rec.list_all()["items"])

    class Snap:
        holdings = pd.DataFrame([{"tradingsymbol": "HELD", "current_value": 150000.0}])
    monkeypatch.setattr(mgr.PortfolioManager, "snapshot", lambda self: Snap())
    monkeypatch.setattr(opt.PortfolioOptimizer, "deploy_cash", lambda self, **k: {
        "buys": [{"ticker": "AAA.NS", "buy_inr": 32000.0, "final_weight_pct": 14.2},
                 {"ticker": "BBB.NS", "buy_inr": 18000.0, "final_weight_pct": 8.0}]})
    assert _size(A, rec)["status"] == "done"

    by = {i["symbol"]: i for i in rec.list_all()["items"]}
    assert by["AAA"]["suggested_amount"] == 32000 and by["AAA"]["suggested_weight_pct"] == 14.2
    assert by["BBB"]["suggested_amount"] == 18000 and by["BBB"]["suggested_weight_pct"] == 8.0
    assert all(i["sized_for_cash"] == 50000 for i in by.values())


def test_a_name_the_optimiser_wont_fund_says_so(queue, monkeypatch):
    """"Good idea, but not alongside what you own" is an answer, not a gap."""
    import importlib, sys
    import src.portfolio.manager as mgr
    import src.portfolio.optimizer as opt
    rec = queue
    A = sys.modules.get("src.dashboard.app") or importlib.import_module("src.dashboard.app")
    rec.record([{"symbol": "JUNK"}], "macro-ideas", run_id="i1")

    class Snap:
        holdings = pd.DataFrame([{"tradingsymbol": "HELD", "current_value": 150000.0}])
    monkeypatch.setattr(mgr.PortfolioManager, "snapshot", lambda self: Snap())
    monkeypatch.setattr(opt.PortfolioOptimizer, "deploy_cash", lambda self, **k: {"buys": []})
    _size(A, rec)

    item = rec.list_all()["items"][0]
    assert item["suggested_amount"] == 0
    assert "wouldn't fund" in item["sizing_note"]


def test_sizing_needs_an_amount(queue):
    import importlib, sys
    A = sys.modules.get("src.dashboard.app") or importlib.import_module("src.dashboard.app")
    assert "error" in A.api_recommendations_optimize({"cash": 0})


def test_recommendations_report_their_age_and_expire(queue):
    rec = queue
    rec.record([{"symbol": "AAA"}], "macro-ideas", run_id="i1")
    assert rec.list_all()["items"][0]["age_label"] == "just now"

    # Age one past the limit and confirm it is pruned on the next read.
    data = json.loads((rec._store_path()).read_text())
    data["items"][0]["created_at"] = (datetime.now() - timedelta(days=61)).isoformat()
    rec._store_path().write_text(json.dumps(data))
    assert rec.list_all()["items"] == []

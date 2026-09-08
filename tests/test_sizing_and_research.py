"""Placeable share counts, and the research dossier behind each recommendation.

The sizing complaint was concrete: an optimiser works in continuous weights and
will return ₹400 for a stock trading at ₹600 — a number you cannot act on, but
which reads like an instruction.
"""
import pytest

from src.portfolio.sizing import to_whole_shares


def _prices(**kw):
    return lambda t: kw.get(t)


def test_an_allocation_too_small_for_one_share_is_rounded_up_not_shown_as_rupees():
    """The reported case: ₹400 allocated to a ₹600 stock."""
    out = to_whole_shares(
        [{"ticker": "AAA.NS", "buy_inr": 400.0, "final_weight_pct": 2.0}],
        _prices(**{"AAA.NS": 600.0}), cash=20000)
    a = out["allocations"][0]
    assert a["shares"] == 1 and a["amount"] == 600.0


def test_it_is_reported_as_unaffordable_when_there_is_no_spare_cash():
    out = to_whole_shares(
        [{"ticker": "AAA.NS", "buy_inr": 400.0, "final_weight_pct": 2.0}],
        _prices(**{"AAA.NS": 600.0}), cash=400)
    assert not out["allocations"]
    assert "not enough for a single share" in out["unaffordable"][0]["reason"]


def test_the_basket_never_exceeds_the_budget():
    out = to_whole_shares(
        [{"ticker": f"S{i}.NS", "buy_inr": 9000.0, "final_weight_pct": 20.0}
         for i in range(5)],
        _prices(**{f"S{i}.NS": 700.0 for i in range(5)}), cash=20000)
    assert out["totals"]["deployed"] <= 20000


def test_spare_cash_cannot_balloon_a_small_position():
    """Freed cash goes to the highest weights, but never past what the
    optimiser asked for — a 1% idea must not become a 12% position."""
    out = to_whole_shares(
        [{"ticker": "CHEAP.NS", "buy_inr": 100.0, "final_weight_pct": 1.0},
         {"ticker": "BIG.NS", "buy_inr": 9000.0, "final_weight_pct": 90.0}],
        _prices(**{"CHEAP.NS": 10.0, "BIG.NS": 4000.0}), cash=50000)
    by = {a["symbol"]: a for a in out["allocations"]}
    assert by["CHEAP"]["amount"] == 100.0            # not thousands
    assert out["totals"]["leftover"] > 0             # left idle, deliberately


def test_a_name_with_no_price_is_reported_not_guessed():
    out = to_whole_shares(
        [{"ticker": "X.NS", "buy_inr": 5000.0, "final_weight_pct": 10.0}],
        _prices(), cash=20000)
    assert out["unpriced"][0]["symbol"] == "X"
    assert not out["allocations"]


def test_every_returned_line_is_placeable():
    out = to_whole_shares(
        [{"ticker": "A.NS", "buy_inr": 400.0, "final_weight_pct": 2.0},
         {"ticker": "B.NS", "buy_inr": 11800.0, "final_weight_pct": 40.0},
         {"ticker": "C.NS", "buy_inr": 7300.0, "final_weight_pct": 25.0}],
        _prices(**{"A.NS": 600.0, "B.NS": 120.0, "C.NS": 2500.0}), cash=20000)
    for a in out["allocations"]:
        assert a["shares"] >= 1
        assert a["amount"] == pytest.approx(a["shares"] * a["price"])


# ---------------------------------------------------------------------------
# Research dossier
# ---------------------------------------------------------------------------
def test_research_survives_every_source_failing(monkeypatch):
    """A dossier with four of six sections is useful; a crash is not."""
    import src.portfolio.research as R

    def boom(*a, **k):
        raise RuntimeError("network down")
    import src.tools.deep_dive as dd
    import src.tools.web_search as ws
    import src.tools.macro as macro
    monkeypatch.setattr(dd, "deep_dive", boom)
    monkeypatch.setattr(ws, "WebSearcher", boom)
    monkeypatch.setattr(macro.MacroSnapshot, "market_mode", boom)

    d = R.research("AAA", with_llm=False)
    assert d["symbol"] == "AAA"
    assert d["gaps"], "what failed must be named, not silently empty"


def test_research_names_its_gaps_rather_than_hiding_them(monkeypatch):
    """Only the news source fails; the rest of the dossier must still build.

    deep_dive is stubbed because it genuinely fetches filings and calls the
    model — a unit test must not depend on the internet or on an LLM being up.
    """
    import src.portfolio.research as R
    import src.tools.deep_dive as dd
    import src.tools.web_search as ws
    monkeypatch.setattr(dd, "deep_dive", lambda sym, max_docs=2: {
        "fundamentals": {"pe": 20.0}, "entry": {"current": 100.0},
        "analysis": {"financial_health": "fine"}, "sources": []})
    monkeypatch.setattr(ws, "WebSearcher",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("x")))
    d = R.research("AAA", macro={}, calendar=None, with_llm=False)
    assert any("news" in g for g in d["gaps"])
    assert d["fundamentals"]["pe"] == 20.0      # the rest still assembled


def test_only_high_impact_upcoming_events_are_attached():
    import src.portfolio.research as R
    cal = {"today": "2026-09-08", "events": [
        {"date": "2026-09-01", "title": "past", "importance": "HIGH"},
        {"date": "2026-09-20", "title": "minor", "importance": "MEDIUM"},
        {"date": "2026-09-16", "title": "FOMC", "importance": "HIGH"},
    ]}
    got = R._events_for("AAA", None, cal)
    assert [e["title"] for e in got] == ["FOMC"]


def test_dossiers_attach_to_pending_recommendations(tmp_path, monkeypatch):
    from src.portfolio import recommendations as rec
    monkeypatch.setattr(rec, "_store_path", lambda: tmp_path / "rec.json")
    rec.record([{"symbol": "AAA"}], "macro-ideas", run_id="i1")
    rec.attach_research({"AAA": {"symbol": "AAA", "counts": {"news": 5}}})
    item = rec.list_all()["items"][0]
    assert item["research"]["counts"]["news"] == 5
    assert item["researched_at"]


def test_the_size_action_also_researches_each_name(tmp_path, monkeypatch):
    """"Size these" must do both — sizing AND a dossier per name.

    Stubbed end to end: the real pass takes about a minute per stock.
    """
    import importlib, sys, time
    import pandas as pd
    import src.portfolio.manager as mgr
    import src.portfolio.optimizer as opt
    import src.portfolio.research as R
    import src.data.market_data as mdm
    from src.portfolio import recommendations as rec

    monkeypatch.setattr(rec, "_store_path", lambda: tmp_path / "rec.json")
    rec.record([{"symbol": "AAA"}], "macro-ideas", run_id="i1")

    class Snap:
        holdings = pd.DataFrame([{"tradingsymbol": "HELD", "current_value": 100000.0}])
    monkeypatch.setattr(mgr.PortfolioManager, "snapshot", lambda self: Snap())
    monkeypatch.setattr(opt.PortfolioOptimizer, "deploy_cash", lambda self, **k: {
        "buys": [{"ticker": "AAA.NS", "buy_inr": 5000.0, "final_weight_pct": 5.0}]})
    monkeypatch.setattr(mdm.MarketData, "ltp", lambda self, t, ik=None: 600.0)
    monkeypatch.setattr(R, "research", lambda sym, **k: {
        "symbol": sym, "counts": {"news": 9, "reports": 2, "social": 0, "events": 1},
        "verdict": {"verdict": "ACCUMULATE", "thesis": "weighed together"}})

    A = sys.modules.get("src.dashboard.app") or importlib.import_module("src.dashboard.app")
    jid = A.api_recommendations_optimize({"cash": 20000, "research": True})["job_id"]
    for _ in range(200):
        if A.JOBS[jid]["status"] != "running":
            break
        time.sleep(0.05)
    assert A.JOBS[jid]["status"] == "done", A.JOBS[jid].get("error")

    item = rec.list_all()["items"][0]
    # floor(5000/600) = 8, topped up to the cap of ceil(5000/600) = 9.
    assert item["suggested_shares"] == 9
    assert item["suggested_amount"] == 5400.0       # 9 x 600, a placeable number
    assert item["research"]["verdict"]["verdict"] == "ACCUMULATE"
    assert item["research"]["counts"]["news"] == 9

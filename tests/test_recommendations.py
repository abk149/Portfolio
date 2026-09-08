"""The recommendation queue: engine output → ghost book → per-engine scorecard.

The point of the ghost portfolio is to judge the ENGINES. That only holds if
recommendations arrive on their own — the moment you have to retype a symbol you
liked, your own selection is back in the loop and the track record measures you.
"""
import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from src.portfolio import ghost, recommendations as rec
    monkeypatch.setattr(rec, "_store_path", lambda: tmp_path / "rec.json")
    monkeypatch.setattr(ghost, "_store_path", lambda: tmp_path / "ghost.json")
    monkeypatch.setattr(ghost, "_price",
                        lambda s: {"AAA": 100.0, "BBB": 250.0, "CCC": 40.0}.get(s))
    return rec, ghost


def test_each_engine_records_with_its_provenance(store):
    rec, _ = store
    rec.record([{"symbol": "AAA", "conviction": "HIGH", "rationale": "why"}],
               "macro-ideas", run_id="r1")
    rec.record([{"symbol": "BBB"}], "dr-quant", run_id="q1")
    rec.record([{"symbol": "CCC", "suggested_amount": 12000}], "optimizer")
    items = rec.list_all()["items"]
    assert {i["symbol"] for i in items} == {"AAA", "BBB", "CCC"}
    assert {i["source_label"] for i in items} == {
        "Macro Ideas", "DR-Quant funnel", "Cash optimiser"}


def test_rerunning_an_engine_does_not_duplicate_its_call(store):
    rec, _ = store
    rec.record([{"symbol": "AAA"}], "macro-ideas", run_id="r1")
    out = rec.record([{"symbol": "AAA"}], "macro-ideas", run_id="r1")
    assert out["added"] == 0
    assert len(rec.list_all()["items"]) == 1


def test_a_new_run_is_a_new_recommendation(store):
    rec, _ = store
    rec.record([{"symbol": "AAA"}], "macro-ideas", run_id="r1")
    rec.record([{"symbol": "AAA"}], "macro-ideas", run_id="r2")
    assert len(rec.list_all()["items"]) == 2


def test_taking_a_recommendation_carries_its_source_into_the_ghost_book(store):
    """Attribution depends entirely on this — if the source is lost at the buy,
    the scorecard can't tell the engines apart."""
    import sys, importlib
    rec, ghost = store
    A = importlib.import_module("src.dashboard.app")
    A = sys.modules["src.dashboard.app"]

    rec.record([{"symbol": "CCC", "suggested_amount": 12000}], "optimizer")
    rid = rec.list_all()["items"][0]["id"]
    res = A.api_recommendation_take({"id": rid})
    assert res["position"]["source"] == "optimizer"
    assert res["position"]["qty"] == 300          # used the SUGGESTED amount
    assert rec.list_all()["items"][0]["status"] == "taken"


def test_the_suggested_amount_can_be_overridden(store):
    import sys, importlib
    rec, _ = store
    A = sys.modules.get("src.dashboard.app") or importlib.import_module("src.dashboard.app")
    rec.record([{"symbol": "AAA", "suggested_amount": 5000}], "macro-ideas")
    rid = rec.list_all()["items"][0]["id"]
    assert A.api_recommendation_take({"id": rid, "amount": 20000})["position"]["qty"] == 200


def test_a_recommendation_with_no_amount_asks_for_one(store):
    import sys, importlib
    rec, _ = store
    A = sys.modules.get("src.dashboard.app") or importlib.import_module("src.dashboard.app")
    rec.record([{"symbol": "AAA"}], "dr-quant")
    rid = rec.list_all()["items"][0]["id"]
    assert "error" in A.api_recommendation_take({"id": rid})


def test_dismissed_recommendations_are_kept_not_deleted(store):
    """What the system suggested and what you did about it IS the dataset."""
    import sys, importlib
    rec, _ = store
    A = sys.modules.get("src.dashboard.app") or importlib.import_module("src.dashboard.app")
    rec.record([{"symbol": "AAA"}], "dr-quant")
    rid = rec.list_all()["items"][0]["id"]
    A.api_recommendation_dismiss({"id": rid})
    assert rec.list_all()["counts"]["dismissed"] == 1
    assert len(rec.list_all()["items"]) == 1


def test_scorecard_separates_the_engines(store, monkeypatch):
    import sys, importlib
    rec, ghost = store
    A = sys.modules.get("src.dashboard.app") or importlib.import_module("src.dashboard.app")

    rec.record([{"symbol": "AAA", "suggested_amount": 20000}], "macro-ideas")
    rec.record([{"symbol": "CCC", "suggested_amount": 12000}], "optimizer")
    for i in rec.list_all()["items"]:
        A.api_recommendation_take({"id": i["id"]})

    # AAA +15%, CCC -10%
    monkeypatch.setattr(ghost, "_price",
                        lambda s: {"AAA": 115.0, "CCC": 36.0}.get(s))
    by = {r["source"]: r for r in A.api_ghost()["attribution"]["by_source"]}
    assert by["macro-ideas"]["return_pct"] == 15.0
    assert by["optimizer"]["return_pct"] == -10.0


def test_your_own_picks_are_scored_separately(store, monkeypatch):
    """So a good manual call can't be credited to an engine."""
    import sys, importlib
    rec, ghost = store
    A = sys.modules.get("src.dashboard.app") or importlib.import_module("src.dashboard.app")
    ghost.buy("AAA", amount=10000, source="manual")
    by = {r["source"]: r for r in A.api_ghost()["attribution"]["by_source"]}
    assert by["manual"]["label"] == "Your own pick"


def test_hit_rate_counts_only_closed_positions(store, monkeypatch):
    """An open loser hasn't lost yet; counting it would flatter whichever
    engine you happened not to sell."""
    import sys, importlib
    rec, ghost = store
    A = sys.modules.get("src.dashboard.app") or importlib.import_module("src.dashboard.app")
    pid = ghost.buy("AAA", amount=10000, source="dr-quant")["position"]["id"]
    ghost.buy("BBB", amount=25000, source="dr-quant")
    monkeypatch.setattr(ghost, "_price", lambda s: {"AAA": 120.0, "BBB": 200.0}.get(s))
    ghost.sell(pid)                                   # one closed winner
    by = {r["source"]: r for r in A.api_ghost()["attribution"]["by_source"]}
    assert by["dr-quant"]["hit_rate_pct"] == 100.0    # not 50% — BBB is still open
    assert by["dr-quant"]["n_closed"] == 1

"""One pipeline for every candidate, and a bias against chasing.

Two policies are enforced here:

  * whatever engine suggested a name, it gets the SAME analysis — otherwise the
    paper track record measures which door an idea came through rather than the
    idea;
  * a stock that has already made its move is not an opportunity. By the time
    the reason is in the news it is in the price, so "already ran" is a
    BLOCKING check, not a footnote.
"""
import numpy as np
import pandas as pd
import pytest

from src.portfolio.screening import uniform_gate
from src.screener.technical import technical_score


def _frame(path):
    p, rows = 100.0, []
    for r in path:
        p *= 1 + r
        rows.append(p)
    idx = pd.date_range(end="2026-09-18", periods=len(rows), freq="B")
    c = pd.Series(rows, index=idx)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99,
                         "close": c, "volume": 1000}, index=idx)


def _paths():
    rng = np.random.default_rng(0)
    base = list(rng.normal(0.0006, 0.010, 300))
    return (base + list(rng.normal(0.0002, 0.008, 63)),     # paused uptrend
            base + list(rng.normal(0.0085, 0.010, 63)))     # already ran


# ---------------------------------------------------------------------------
# The scanner must stop preferring stocks that already moved
# ---------------------------------------------------------------------------
def test_a_paused_uptrend_now_outranks_a_parabolic_one():
    """The old scoring added up to 25 points for recent returns plus 10 for
    sitting at the 52-week high, so it systematically surfaced names AFTER the
    move. That ranking is now reversed."""
    paused, ran = _paths()
    assert technical_score(_frame(paused))["score"] > technical_score(_frame(ran))["score"]


def test_an_overextended_stock_is_flagged_for_downstream_stages():
    _, ran = _paths()
    t = technical_score(_frame(ran))
    assert t["already_moved"], "a parabolic run must be visible, not just scored"
    assert t["move_left"] is False


def test_a_healthy_trend_is_not_flagged():
    paused, _ = _paths()
    t = technical_score(_frame(paused))
    assert t["already_moved"] == [] and t["move_left"] is True


def test_a_broken_stock_does_not_score_well_either():
    """Anti-chasing must not turn into buying falling knives."""
    rng = np.random.default_rng(3)
    falling = list(rng.normal(-0.003, 0.012, 363))
    paused, _ = _paths()
    assert technical_score(_frame(falling))["score"] < technical_score(_frame(paused))["score"]


# ---------------------------------------------------------------------------
# The uniform gate
# ---------------------------------------------------------------------------
GOOD_F = dict(pe=20, roe_pct=18, debt_to_equity=0.4, sales_growth_pct=12, sector="IT")
ROOM = dict(stance="room left", reading="23% below its high", runway_score=80)


def test_a_good_name_with_room_passes():
    assert uniform_gate("X", GOOD_F, ROOM)["passed"] is True


def test_a_good_name_that_already_ran_is_blocked():
    """The policy check: strong fundamentals do not excuse a finished move."""
    g = uniform_gate("X", GOOD_F, dict(stance="move largely made",
                                       reading="up 60% in 3 months", runway_score=20))
    assert g["passed"] is False
    assert any("move" in b["label"].lower() for b in g["blocking"])


def test_leverage_blocks_an_industrial_but_not_a_bank():
    levered = dict(GOOD_F, debt_to_equity=2.4, sector="Metals")
    assert uniform_gate("X", levered, ROOM)["passed"] is False
    bank = dict(GOOD_F, debt_to_equity=6.0, sector="Private Bank")
    assert uniform_gate("X", bank, ROOM)["passed"] is True


def test_missing_data_blocks_rather_than_passes_silently():
    g = uniform_gate("X", {"sector": "IT"}, ROOM)
    assert g["passed"] is False


def test_an_untestable_check_is_neither_pass_nor_fail():
    """Unknown must stay unknown — treating it as a pass is how bad names slip
    through, and as a fail is how good ones get dropped."""
    g = uniform_gate("X", dict(pe=20, roe_pct=18, sector="IT"), ROOM)
    statuses = {c["name"]: c["status"] for c in g["checks"]}
    assert statuses["leverage"] == "unknown"
    assert "Debt under control" in g["untested"]


def test_every_candidate_gets_the_same_checks():
    """Identical shape regardless of which engine suggested it."""
    a = uniform_gate("A", GOOD_F, ROOM)
    b = uniform_gate("B", dict(GOOD_F, sector="Metals"), ROOM)
    assert [c["name"] for c in a["checks"]] == [c["name"] for c in b["checks"]]


# ---------------------------------------------------------------------------
# Blocking flows through to the queue
# ---------------------------------------------------------------------------
def test_a_failing_name_is_moved_out_of_the_buy_queue(tmp_path, monkeypatch):
    from src.portfolio import recommendations as rec
    monkeypatch.setattr(rec, "_store_path", lambda: tmp_path / "rec.json")
    rec.record([{"symbol": "AAA"}, {"symbol": "BBB"}], "macro-ideas", run_id="i1")
    rec.attach_research({
        "AAA": {"symbol": "AAA", "gate": uniform_gate("AAA", GOOD_F, ROOM)},
        "BBB": {"symbol": "BBB", "gate": uniform_gate(
            "BBB", GOOD_F, dict(stance="move largely made", reading="ran", runway_score=10))},
    })
    by = {i["symbol"]: i for i in rec.list_all()["items"]}
    assert by["AAA"]["status"] == "pending"
    assert by["BBB"]["status"] == "blocked"
    assert "move" in by["BBB"]["blocked_reason"].lower()


def test_a_blocked_name_reopens_when_it_later_passes(tmp_path, monkeypatch):
    """A stock that pulls back should become investable again."""
    from src.portfolio import recommendations as rec
    monkeypatch.setattr(rec, "_store_path", lambda: tmp_path / "rec.json")
    rec.record([{"symbol": "AAA"}], "dr-quant", run_id="q1")
    rec.attach_research({"AAA": {"gate": uniform_gate(
        "AAA", GOOD_F, dict(stance="move largely made", reading="ran", runway_score=10))}})
    assert rec.list_all()["items"][0]["status"] == "blocked"

    rec.attach_research({"AAA": {"gate": uniform_gate("AAA", GOOD_F, ROOM)}})
    item = rec.list_all()["items"][0]
    assert item["status"] == "pending" and "blocked_reason" not in item


# ---------------------------------------------------------------------------
# Factor sensitivity
# ---------------------------------------------------------------------------
def test_a_planted_currency_exposure_is_recovered(monkeypatch):
    """If a stock is built to move -1.5x with USD/INR, that is what should
    come back — the measurement has to be trustworthy before the LLM is told
    to prefer it over sector intuition."""
    import src.portfolio.factors as F

    rng = np.random.default_rng(1)
    idx = pd.date_range(end="2026-09-18", periods=600, freq="B")
    nifty_r = rng.normal(0.0004, 0.008, len(idx))
    fx_r = rng.normal(0.0001, 0.004, len(idx))
    stock_r = 1.0 * nifty_r - 1.5 * fx_r + rng.normal(0, 0.002, len(idx))

    def _cum(r):
        return pd.Series(100 * np.cumprod(1 + r), index=idx)

    book = {"AAA.NS": _cum(stock_r), "^NSEI": _cum(nifty_r),
            "USDINR=X": _cum(fx_r), "CL=F": _cum(rng.normal(0, 0.01, len(idx))),
            "^TNX": _cum(rng.normal(0, 0.01, len(idx)))}
    monkeypatch.setattr(F, "_closes", lambda t, d: book.get(t, pd.Series(dtype=float)))

    out = F.sensitivities("AAA", years=3)
    joint = {r["factor"]: r["beta_joint"] for r in out["factors"]}
    assert joint["usdinr"] == pytest.approx(-1.5, abs=0.25)
    assert joint["nifty"] == pytest.approx(1.0, abs=0.25)
    assert out["r_squared"] > 0.8


def test_series_on_different_market_calendars_still_align(monkeypatch):
    """Yahoo stamps bars at each market's OPEN, so Indian equities, the rupee
    and US yields arrive on different timestamps. Without normalising to plain
    dates the intersection is empty and the answer reads as "no relationship"
    rather than "no overlap"."""
    import src.portfolio.factors as F
    rng = np.random.default_rng(2)
    n = 400
    ist = pd.date_range(end="2026-09-18 03:45", periods=n, freq="B", tz="UTC")
    est = pd.date_range(end="2026-09-18 13:30", periods=n, freq="B", tz="UTC")
    s1 = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, n)), index=ist)
    s2 = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, n)), index=est)

    def closes(t, d):
        raw = s1 if t.endswith(".NS") else s2
        i = pd.to_datetime(raw.index).tz_convert("Asia/Kolkata").tz_localize(None)
        out = raw.copy(); out.index = i.normalize()
        return out[~out.index.duplicated(keep="last")]
    monkeypatch.setattr(F, "_closes", closes)

    out = F.sensitivities("AAA", years=3)
    assert "error" not in out and out["weeks"] > 50


def test_runway_calls_a_stretched_stock_what_it_is(monkeypatch):
    import src.portfolio.factors as F
    rng = np.random.default_rng(4)
    idx = pd.date_range(end="2026-09-18", periods=400, freq="B")
    path = list(rng.normal(0.0003, 0.008, 330)) + list(rng.normal(0.010, 0.008, 70))
    monkeypatch.setattr(F, "_closes", lambda t, d: pd.Series(
        100 * np.cumprod(1 + np.array(path)), index=idx))
    r = F.runway("AAA")
    assert r["stance"] == "move largely made"
    assert r["already_moved"]


def test_runway_sees_room_in_a_pullback(monkeypatch):
    import src.portfolio.factors as F
    rng = np.random.default_rng(5)
    idx = pd.date_range(end="2026-09-18", periods=400, freq="B")
    path = list(rng.normal(0.0012, 0.008, 300)) + list(rng.normal(-0.0015, 0.008, 100))
    monkeypatch.setattr(F, "_closes", lambda t, d: pd.Series(
        100 * np.cumprod(1 + np.array(path)), index=idx))
    r = F.runway("AAA")
    assert r["stance"] in ("room left", "mixed")
    assert r["from_52w_high_pct"] < -5

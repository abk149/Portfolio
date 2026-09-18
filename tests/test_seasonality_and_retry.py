"""Seasonality analysis, and repairing a partial research dossier.

The seasonality tests care most about NOT overclaiming: twelve monthly averages
drawn from five years will always show some months looking strong, and calling
that a tradeable pattern would be the most misleading thing the app could do.
"""
import numpy as np
import pandas as pd
import pytest

from src.portfolio import seasonality as S


def _series(years=5, monthly_bias=None, seed=0):
    """Daily closes with an optional per-calendar-month drift."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(end=pd.Timestamp("2026-08-31"), periods=years * 365, freq="D")
    out, price = [], 100.0
    for d in idx:
        bias = (monthly_bias or {}).get(d.month, 0.0) / 21.0     # per trading day
        price *= 1 + bias + rng.normal(0, 0.004)
        out.append(price)
    return pd.Series(out, index=idx)


@pytest.fixture()
def prices(monkeypatch):
    def _install(s):
        monkeypatch.setattr(S, "_prices", lambda symbol, years: s)
    return _install


def test_a_planted_pattern_is_found(prices):
    """April strongly positive every year should be detected."""
    prices(_series(monthly_bias={4: 0.06}))
    r = S.seasonality("AAA", years=5)
    assert r["best_month"]["label"] == "Apr"
    assert r["verdict"] == "possible seasonality"
    assert "Apr" in r["standouts"]


def test_pure_noise_is_rarely_called_seasonal(monkeypatch):
    """The important one, measured properly rather than on a single lucky seed.

    Twelve months are examined at once, so something always LOOKS good on
    random data — the multiple-comparisons trap. The gate is calibrated so that
    a false "this stock is seasonal" is rare, because a missed edge costs an
    opportunity while an invented one costs money.
    """
    false_positives = 0
    runs = 25
    for seed in range(runs):
        monkeypatch.setattr(S, "_prices", lambda a, b, _s=seed: _series(seed=_s))
        if S.seasonality("X", years=5)["verdict"] == "possible seasonality":
            false_positives += 1
    assert false_positives / runs <= 0.08, (
        f"{false_positives}/{runs} noise series were called seasonal")


def test_a_real_pattern_is_still_found_reliably(monkeypatch):
    """The gate must not be so strict that it never fires."""
    hits = 0
    runs = 10
    for seed in range(runs):
        monkeypatch.setattr(S, "_prices",
                            lambda a, b, _s=seed: _series(monthly_bias={4: 0.06}, seed=_s))
        if "Apr" in S.seasonality("X", years=5)["standouts"]:
            hits += 1
    assert hits == runs


def test_a_single_dramatic_year_does_not_make_a_pattern(prices, monkeypatch):
    """One huge March and four flat ones is an outlier, not seasonality.

    Consistency is required as well as size — a big average backed by one year
    is exactly what noise looks like.
    """
    monthly = pd.DataFrame([
        {"_ym": 202000 + m, "year": 2020 + (m // 13), "month": ((m - 1) % 12) + 1,
         "ret": 40.0 if (m == 3) else 0.1}
        for m in range(1, 61)
    ])
    # A real DatetimeIndex: seasonality filters by date before grouping.
    monkeypatch.setattr(S, "_prices", lambda s, y: pd.Series(
        [1.0, 2.0], index=pd.to_datetime(["2024-01-01", "2024-02-01"])))
    monkeypatch.setattr(S, "_monthly_returns", lambda p: monthly)
    r = S.seasonality("AAA", years=5)
    assert "Mar" not in r["standouts"]


def test_short_history_refuses_to_judge(prices):
    prices(_series(years=2))
    r = S.seasonality("AAA", years=2)
    assert r["verdict"] == "not enough history"


def test_no_history_is_reported_not_crashed(monkeypatch):
    monkeypatch.setattr(S, "_prices", lambda s, y: pd.Series(dtype=float))
    assert "error" in S.seasonality("NOPE", years=5)


def test_sample_size_is_always_reported(prices):
    prices(_series())
    r = S.seasonality("AAA", years=5)
    assert r["observations_per_month"] >= 4
    assert all(m["n"] is not None for m in r["by_month"])
    assert "randomness" in r["caveat"]


def test_year_by_year_matrix_lets_an_outlier_be_seen(prices):
    prices(_series(years=4))
    r = S.seasonality("AAA", years=4)
    assert len(r["by_year"]) >= 4 and "Jan" in r["by_year"][0]


# ---------------------------------------------------------------------------
# Repairing a partial dossier
# ---------------------------------------------------------------------------
@pytest.fixture()
def research_env(monkeypatch):
    import src.llm.insights as ins
    import src.tools.deep_dive as dd
    import src.tools.web_search as ws
    monkeypatch.setattr(dd, "deep_dive", lambda sym, max_docs=2: {
        "fundamentals": {"pe": 20.0}, "entry": {"current": 100.0},
        "analysis": {"financial_health": "fine"},
        "sources": [{"title": "Q1 results"}]})
    monkeypatch.setattr(ws, "WebSearcher", lambda **k: type("W", (), {
        "news_for": lambda self, s, c=None: [{"title": "n", "source": "ET"}]})())
    return ins


def test_a_failed_judgement_is_reported_with_its_retry_scope(research_env):
    import src.portfolio.research as R
    research_env._complete = lambda sy, pr: {"ok": False, "error": "empty response"}
    d = R.research("AAA", macro={}, calendar=None)
    assert d["complete"] is False
    assert d["failed_sections"] == ["judgement"]
    assert d["retryable"] == ["judgement"]


def test_rejudging_reuses_the_stored_dossier(research_env):
    """The point of scoped retry: don't re-fetch filings to fix the model."""
    import src.portfolio.research as R
    research_env._complete = lambda sy, pr: {"ok": False, "error": "empty"}
    first = R.research("AAA", macro={}, calendar=None)

    calls = {"deep_dive": 0}
    import src.tools.deep_dive as dd
    dd.deep_dive = lambda *a, **k: calls.__setitem__("deep_dive", calls["deep_dive"] + 1)
    research_env._complete = lambda sy, pr: {
        "ok": True, "text": '{"verdict":"BUY","conviction":"HIGH","thesis":"t"}'}

    again = R.research("AAA", macro={}, calendar=None, scope="judgement", previous=first)
    assert again["complete"] is True
    assert again["verdict"]["verdict"] == "BUY"
    assert calls["deep_dive"] == 0, "filings must not be re-fetched"
    assert again["counts"]["reports"] == 1, "the stored filings survive"


def test_rejudging_without_a_stored_dossier_says_so():
    import src.portfolio.research as R
    out = R.research("AAA", scope="judgement", previous=None)
    assert "error" in out


def test_a_documents_failure_offers_the_documents_retry(research_env, monkeypatch):
    import src.portfolio.research as R
    import src.tools.deep_dive as dd
    monkeypatch.setattr(dd, "deep_dive",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bse timeout")))
    research_env._complete = lambda sy, pr: {"ok": True, "text": '{"verdict":"WATCH"}'}
    d = R.research("AAA", macro={}, calendar=None)
    assert d["retryable"] == ["documents"]

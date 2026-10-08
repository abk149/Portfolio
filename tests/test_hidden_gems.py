"""Hidden Gems engine: synthetic series, no network.

The whole point of the engine is to find the stock that has NOT run yet —
so the tests build the three shapes that must be told apart:

  (a) a textbook long base with accumulation + fundamental inflection → scores high
  (b) a parabolic stock sitting at its 52-week high → hard-rejected
  (c) a freefall value-trap, >50% below its high with a falling 200EMA → hard-rejected

plus the API contract: everything must survive the strict JSON encoder.
"""
import json

import numpy as np
import pandas as pd
import pytest

from src.screener.hidden_gems import GemConfig, evaluate


def _frame(closes, vols):
    c = np.asarray(closes, dtype=float)
    idx = pd.date_range("2022-01-03", periods=len(c), freq="B")
    return pd.DataFrame({
        "open": c,
        "high": c * 1.005,
        "low": c * 0.995,
        "close": c,
        "volume": np.asarray(vols, dtype=float),
    }, index=idx)


def _index_series(n=500, daily_pct=0.0005):
    """A steadily rising index, so relative strength is computable."""
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    return pd.Series(100 * (1 + daily_pct) ** np.arange(n), index=idx)


def textbook_base():
    """Old high ~19 months ago, long tightening base, volume dry-up, then a
    gentle turn up on expanding volume with OBV rising."""
    n1, n2, n3 = 100, 385, 15
    seg1 = np.linspace(80, 150, n1)                       # the old run
    amp = np.linspace(4.0, 1.0, n2)                       # volatility contracting
    seg2 = 100 + amp * np.sin(np.arange(n2) / 7.0)        # the long base
    seg3 = np.linspace(100, 106, n3)                      # the quiet turn up
    closes = np.concatenate([seg1, seg2, seg3])
    vols = np.concatenate([
        np.full(n1, 80_000.0),
        np.full(300, 80_000.0),                           # early base: active
        np.full(n2 - 300, 20_000.0),                      # late base: dried up
        np.full(n3, 90_000.0),                            # turn: expansion
    ])
    df = _frame(closes, vols)
    # the base's daily range contracts with its amplitude (otherwise the
    # constant ±0.5% spread from _frame would mask the tightening in ATR)
    df.iloc[n1:n1 + n2, df.columns.get_loc("high")] = seg2 + amp * 0.6
    df.iloc[n1:n1 + n2, df.columns.get_loc("low")] = seg2 - amp * 0.6
    return df


GOOD_FUNDAMENTALS = {"pe": 18.0, "roe_pct": 18.0, "debt_to_equity": 0.4,
                     "sales_growth_pct": 14.0, "profit_growth_pct": 22.0,
                     "sector": "IT"}


def _signals_by_key(res, kind):
    return {s["key"]: s for s in res[kind]}


def test_textbook_base_scores_high_and_fires_the_expected_signals():
    res = evaluate(textbook_base(), index_close=_index_series(),
                   fundamentals=GOOD_FUNDAMENTALS, symbol="GEM", name="Gem Co")
    assert res["ok"], res.get("reason")
    assert res["gem_score"] >= 60
    assert res["verdict"] in ("GEM", "STRONG_GEM")

    # the base itself
    assert 4 <= res["base"]["months"] <= 24
    assert -40 <= res["distance_from_52w_high_pct"] <= -5
    assert res["base"]["volatility_contraction"] < 1.0
    assert res["return_12m_pct"] < 30

    tech = _signals_by_key(res, "technical_signals")
    for key in ("obv_divergence", "volume_pattern", "relative_strength",
                "ema_reclaim", "higher_high"):
        assert tech[key]["passed"], f"{key} should have fired: {tech[key]['detail']}"

    fund = _signals_by_key(res, "fundamental_signals")
    for key in ("earnings_growth", "revenue_growth", "roe", "leverage"):
        assert fund[key]["passed"], f"{key} should have fired: {fund[key]['detail']}"

    # the thesis is spelled out, no LLM involved
    assert isinstance(res["summary"], str) and "base" in res["summary"].lower()


def test_parabolic_already_run_stock_is_rejected():
    n = 300
    closes = np.concatenate([np.linspace(100, 110, n - 60),
                             np.linspace(110, 170, 60)])   # vertical into the high
    df = _frame(closes, np.full(n, 100_000.0))
    res = evaluate(df, index_close=_index_series(n),
                   fundamentals=GOOD_FUNDAMENTALS, symbol="HOT")
    assert not res["ok"]
    assert "52-week high" in res["reason"]


def test_freefall_value_trap_is_rejected():
    n = 300
    closes = np.linspace(200, 76, n)                      # -62% and still falling
    df = _frame(closes, np.full(n, 100_000.0))
    res = evaluate(df, index_close=_index_series(n),
                   fundamentals=GOOD_FUNDAMENTALS, symbol="TRAP")
    assert not res["ok"]
    assert "freefall" in res["reason"].lower() or "value-trap" in res["reason"].lower()


def test_illiquid_stock_is_rejected():
    df = textbook_base()
    df["volume"] = 1_000.0                                # untradeable
    res = evaluate(df, fundamentals=GOOD_FUNDAMENTALS, symbol="THIN")
    assert not res["ok"]
    assert "illiquid" in res["reason"].lower()


def test_missing_index_and_fundamentals_degrade_gracefully():
    """No index → RS signal is 'unknown', not a pass or a crash. No
    fundamentals → the fundamental block simply doesn't inflate the score."""
    res = evaluate(textbook_base(), index_close=None, fundamentals=None, symbol="LONE")
    assert res["ok"], res.get("reason")
    rs = _signals_by_key(res, "technical_signals")["relative_strength"]
    assert rs["passed"] is None
    assert res["relative_strength_3m"] is None
    assert res["block_scores"]["fundamental"] is None
    assert res["gem_score"] > 0


def test_result_is_strict_json_safe():
    res = evaluate(textbook_base(), index_close=_index_series(),
                   fundamentals=GOOD_FUNDAMENTALS, symbol="GEM")
    assert res["ok"]
    # exactly the encoder Starlette uses — NaN/Infinity are rejected
    payload = json.dumps(res, allow_nan=False)
    back = json.loads(payload)
    gem = back
    for key in ("symbol", "name", "sector", "price", "gem_score", "verdict",
                "base", "distance_from_52w_high_pct", "distance_from_52w_low_pct",
                "return_6m_pct", "return_12m_pct", "relative_strength_3m",
                "technical_signals", "fundamental_signals", "summary"):
        assert key in gem, f"contract key missing: {key}"
    for s in gem["technical_signals"] + gem["fundamental_signals"]:
        assert set(s) == {"key", "label", "value", "passed", "weight", "detail"}


def test_insufficient_history_is_rejected():
    df = textbook_base().tail(100)
    res = evaluate(df, fundamentals=GOOD_FUNDAMENTALS, symbol="NEW")
    assert not res["ok"]
    assert "insufficient" in res["reason"]

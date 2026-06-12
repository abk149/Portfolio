"""Fundamental scoring — partial-data-aware.

Indian-market data is patchy: NSE always gives P/E + sector, sometimes growth
(from quarterly filings); Yahoo/screener.in add ROE/D/E/margins WHEN reachable.
A naive fixed-point sum would cap a P/E-only stock at ~15/100 and make the
whole universe look terrible.

Instead we score each AVAILABLE signal on its own 0-1 scale, then return the
weighted average of whatever we have — rescaled to 0-100. A stock with only
P/E gets a fair score from that one axis; a stock with the full set gets a
richer score. `coverage` tells you how complete the inputs were.
"""
from __future__ import annotations

from typing import Optional


def _band(x: Optional[float], lo: float, hi: float) -> Optional[float]:
    """1.0 if x is comfortably inside [lo,hi], tapering to 0 outside. None if x is None."""
    if x is None:
        return None
    if lo <= x <= hi:
        return 1.0
    # linear taper over one band-width outside the range
    width = (hi - lo) or 1.0
    if x < lo:
        return max(0.0, 1.0 - (lo - x) / width)
    return max(0.0, 1.0 - (x - hi) / width)


def _higher_better(x: Optional[float], floor: float, target: float) -> Optional[float]:
    """0 at/below floor, 1.0 at/above target, linear between."""
    if x is None:
        return None
    if x <= floor:
        return 0.0
    if x >= target:
        return 1.0
    return (x - floor) / (target - floor)


def fundamental_score(f: dict) -> dict:
    """`f` uses yfinance-style keys (trailingPE, returnOnEquity decimal, etc.).
    Returns {score: 0-100 or None, coverage: 0-1, ...inputs echoed...}."""
    pe = f.get("trailingPE")
    pb = f.get("priceToBook")
    roe = f.get("returnOnEquity")          # decimal, e.g. 0.18
    de = f.get("debtToEquity")             # ratio, e.g. 0.45
    eg = f.get("earningsGrowth")           # decimal
    rg = f.get("revenueGrowth")            # decimal
    pm = f.get("profitMargins")            # decimal
    fcf = f.get("freeCashflow")

    # Each signal → (normalised 0-1 score, weight). None signals are skipped.
    signals: list[tuple[Optional[float], float]] = [
        (_band(pe, 8, 35),              2.0),   # valuation — reasonable P/E
        (_band(pb, 0.5, 8),             1.0),   # valuation — P/B
        (_higher_better(roe, 0.05, 0.20), 2.5), # quality — ROE
        # debt: lower is better — invert. D/E 0 → 1.0, D/E 2 → 0.0
        (None if de is None else max(0.0, 1.0 - de / 2.0), 1.5),
        (_higher_better(eg, -0.05, 0.20), 1.5), # growth — earnings
        (_higher_better(rg, -0.05, 0.15), 1.5), # growth — revenue
        (_higher_better(pm, 0.0, 0.20),   1.0), # quality — margins
        (1.0 if (fcf is not None and fcf > 0) else (0.0 if fcf is not None else None), 1.0),
    ]

    used = [(s, w) for s, w in signals if s is not None]
    if not used:
        return {"score": None, "coverage": 0.0, "reason": "no fundamental data"}

    total_w = sum(w for _, w in used)
    weighted = sum(s * w for s, w in used)
    raw_pct = weighted / total_w * 100        # quality across available signals

    max_w = sum(w for _, w in signals)
    coverage = total_w / max_w                 # 0…1 fraction of signals present

    # CRUCIAL: scale by sqrt(coverage). Eliminates the "every stock with any
    # P/E shows fund_score=100" artefact — a single-signal score now caps at
    # ~35, two signals ~50, full coverage 100. The penalty is proportional to
    # how much we actually KNOW about the company.
    score = round(raw_pct * (coverage ** 0.5), 1)

    return {
        "score": score,
        "coverage": round(coverage, 2),       # 1.0 = had every signal
        "raw_score": round(raw_pct, 1),       # uncalibrated quality, for reference
        "n_signals": len(used),
        "trailingPE": pe, "priceToBook": pb, "ROE": roe, "DE": de,
        "earningsGrowth": eg, "revenueGrowth": rg, "profitMargins": pm,
        "sector": f.get("sector"), "industry": f.get("industry"),
        "marketCap": f.get("marketCap"),
    }

"""One set of tests every candidate must pass, whatever suggested it.

Ideas reach the ghost book from three engines that judge very differently:
Macro Ideas reasons from the top down, DR-Quant validates balance sheets, the
optimiser only cares about covariance. Left alone, a name's scrutiny depended
entirely on which door it came through — so the paper track record would have
measured the doors, not the ideas.

Every candidate now runs the same gate. Checks marked REQUIRED block a
suggestion outright; the rest are recorded as context. A blocked name is kept
with its reason rather than hidden, because "the system considered this and
rejected it" is information.

The last required check is the one that matters most for making money: a stock
that has already made its move is not an opportunity, however good the story.
By the time the reason is in the news, it is in the price.
"""
from __future__ import annotations

from typing import Optional

from src.utils.logger import get_logger

log = get_logger("portfolio.screening")

# Sectors where leverage is the business model, not a warning sign.
_LEVERAGE_EXEMPT = ("bank", "financial", "nbfc", "finance", "insurance", "housing")


def _check(name: str, label: str, passed: Optional[bool], required: bool,
           detail: str, value=None) -> dict:
    return {"name": name, "label": label, "passed": passed, "required": required,
            "detail": detail, "value": value,
            # None means "couldn't test", which is neither a pass nor a fail and
            # must never be silently treated as one.
            "status": "pass" if passed else ("unknown" if passed is None else "fail")}


def uniform_gate(symbol: str, fundamentals: Optional[dict] = None,
                 runway: Optional[dict] = None, sensitivities: Optional[dict] = None,
                 macro: Optional[dict] = None) -> dict:
    """Identical tests for every candidate. Returns pass/fail plus every check."""
    f = fundamentals or {}
    rw = runway or {}
    sector = str(f.get("sector") or "").lower()
    checks: list[dict] = []

    # 1. Do we know enough to judge at all?
    have = [k for k in ("pe", "roe_pct", "debt_to_equity") if f.get(k) is not None]
    checks.append(_check(
        "data", "Enough data to judge", len(have) >= 2, True,
        f"{len(have)} of 3 core metrics available"
        + ("" if len(have) >= 2 else " — too little to assess this name"),
        value=len(have)))

    # 2. Leverage.
    de = f.get("debt_to_equity")
    exempt = any(w in sector for w in _LEVERAGE_EXEMPT)
    if de is None:
        checks.append(_check("leverage", "Debt under control", None, True,
                             "debt-to-equity unavailable"))
    elif exempt:
        checks.append(_check("leverage", "Debt under control", True, True,
                             f"D/E {de} — lending business, leverage expected",
                             value=de))
    else:
        checks.append(_check("leverage", "Debt under control", de <= 1.5, True,
                             f"D/E {de}" + ("" if de <= 1.5 else " — above the 1.5 limit"),
                             value=de))

    # 3. Returns on capital.
    roe = f.get("roe_pct")
    growth = f.get("sales_growth_pct")
    if roe is None:
        checks.append(_check("returns", "Earns on its capital", None, False,
                             "ROE unavailable"))
    else:
        ok = roe >= 8 or (growth is not None and growth >= 15)
        checks.append(_check("returns", "Earns on its capital", ok, True,
                             f"ROE {roe}%"
                             + ("" if ok else " — below 8% with no growth to justify it"),
                             value=roe))

    # 4. The business is not shrinking.
    if growth is None:
        checks.append(_check("growth", "Business not shrinking", None, False,
                             "sales growth unavailable"))
    else:
        checks.append(_check("growth", "Business not shrinking", growth > -10, True,
                             f"sales growth {growth}%"
                             + ("" if growth > -10 else " — contracting sharply"),
                             value=growth))

    # 5. THE POLICY CHECK: is there any move left?
    stance = rw.get("stance")
    if stance is None:
        checks.append(_check("runway", "Move still ahead", None, True,
                             "couldn't measure how far this has already run"))
    else:
        moved = stance == "move largely made"
        checks.append(_check(
            "runway", "Move still ahead", not moved, True,
            (rw.get("reading") or stance), value=rw.get("runway_score")))

    # 6. Context only — recorded, never blocking.
    if sensitivities and sensitivities.get("driven_by"):
        checks.append(_check("exposure", "Macro exposure known", True, False,
                             "moves with " + ", ".join(sensitivities["driven_by"]),
                             value=sensitivities.get("r_squared")))
    else:
        checks.append(_check("exposure", "Macro exposure known", None, False,
                             "no dominant macro driver identified"))

    regime = (macro or {}).get("mode")
    if regime:
        checks.append(_check("regime", "Market backdrop", True, False,
                             f"buying into a {regime} market", value=regime))

    blocking = [c for c in checks if c["required"] and c["status"] == "fail"]
    unknown_required = [c for c in checks if c["required"] and c["status"] == "unknown"]
    passed = not blocking

    if blocking:
        summary = ("Not suggested — fails: "
                   + "; ".join(c["label"].lower() for c in blocking) + ".")
    elif unknown_required:
        summary = ("Suggested with caution — couldn't test: "
                   + ", ".join(c["label"].lower() for c in unknown_required) + ".")
    else:
        summary = "Passes every required check."

    return {
        "symbol": (symbol or "").upper(),
        "passed": passed,
        "checks": checks,
        "blocking": [{"label": c["label"], "detail": c["detail"]} for c in blocking],
        "untested": [c["label"] for c in unknown_required],
        "n_passed": sum(1 for c in checks if c["status"] == "pass"),
        "n_total": len(checks),
        "summary": summary,
    }

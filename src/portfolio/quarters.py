"""The last few quarters, newest first — from the exchange's own filings.

Annual reports are what the document fetcher tends to surface, and they are the
wrong thing to read before a trade: by the time one is published the numbers in
it are up to a year old. What matters is the LATEST quarter and how the three or
four before it trended.

So this reads NSE's quarterly result filings directly. They carry structured
XBRL rather than a PDF, which is better: the numbers come out exactly rather
than being scraped out of prose, and revenue/profit/EPS can be compared quarter
to quarter without an LLM in the loop.

Two traps, both already learned the hard way elsewhere in this codebase and
guarded here:

  * NSE dates arrive as "31-Mar-2024", so sorting them as strings puts December
    before March and "latest quarter" silently returns a year-old filing.
  * One XBRL file repeats the same tag for the quarter, the year-to-date and
    the prior-year period. Comparing a quarter against a nine-month cumulative
    produces growth figures that look spectacular and mean nothing.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from src.utils.logger import get_logger

log = get_logger("portfolio.quarters")

CRORE = 1e7


def _fy_label(end: date) -> str:
    """Indian fiscal quarters: FY runs April-March, so Jun = Q1."""
    q = {6: "Q1", 9: "Q2", 12: "Q3", 3: "Q4"}.get(end.month)
    if q is None:                       # a non-standard period end
        return end.strftime("%b %Y")
    fy = end.year + 1 if end.month > 3 else end.year
    return f"{q} FY{str(fy)[-2:]}"


def _pct(cur: Optional[float], prev: Optional[float]) -> Optional[float]:
    if cur is None or prev is None or prev == 0:
        return None
    g = round((cur - prev) / abs(prev) * 100, 2)
    # A quarter growing >500% almost always means mismatched periods were
    # compared. Drop it rather than print a number that isn't real.
    return g if -95 <= g <= 500 else None


def quarterly_results(symbol: str, n: int = 4) -> dict:
    """The latest `n` quarters with revenue, profit, EPS, margin and growth."""
    from src.data.cache import get_or_set
    sym = (symbol or "").upper().replace(".NS", "").replace(".BO", "")
    if not sym:
        return {"error": "no symbol"}
    n = max(2, min(int(n or 4), 8))

    def _do():
        from src.data.nse_scraper import (_get, _parse_date, _parse_xbrl,
                                          financial_results)
        try:
            filings = financial_results(sym, "Quarterly") or []
        except Exception as e:
            return {"error": f"Couldn't reach the exchange's results feed ({e})."}
        if not filings:
            return {"error": "No quarterly result filings found for this stock."}

        for f in filings:
            f["_end"] = _parse_date(f.get("to_date"))
        filings = [f for f in filings if f["_end"]]
        if not filings:
            return {"error": "Result filings carried no usable dates."}

        # Consolidated where available — it's the whole group, which is what a
        # shareholder owns. Sort on REAL dates, never the strings.
        cons = [f for f in filings if f.get("consolidated")]
        series = cons if len(cons) >= 2 else filings
        series.sort(key=lambda f: f["_end"], reverse=True)

        # Fetch a year beyond what's displayed so each quarter has a
        # same-quarter-last-year comparison.
        rows: list[dict] = []
        for f in series[:n + 4]:
            rev, pat, eps = (f.get("income_inline"), f.get("profit_inline"),
                             f.get("eps_inline"))
            if (rev is None or pat is None) and f.get("xbrl_url"):
                xml = _get(f["xbrl_url"], want_json=False)
                if xml:
                    p = _parse_xbrl(xml)
                    rev = rev if rev is not None else p.get("revenue")
                    pat = pat if pat is not None else p.get("profit")
                    eps = eps if eps is not None else p.get("eps")
            if rev is None and pat is None:
                continue
            rows.append({
                "end": f["_end"], "label": _fy_label(f["_end"]),
                "revenue_cr": round(rev / CRORE, 1) if rev is not None else None,
                "pat_cr": round(pat / CRORE, 1) if pat is not None else None,
                "eps": round(eps, 2) if eps is not None else None,
                "audited": f.get("audited"),
                "consolidated": bool(f.get("consolidated")),
            })

        if not rows:
            return {"error": "Result filings found, but none carried readable "
                             "revenue or profit figures."}

        for i, q in enumerate(rows):
            q["net_margin_pct"] = (
                round(q["pat_cr"] / q["revenue_cr"] * 100, 2)
                if q.get("revenue_cr") and q.get("pat_cr") is not None
                and q["revenue_cr"] != 0 else None)
            prev = rows[i + 1] if i + 1 < len(rows) else None
            if prev:
                q["revenue_qoq_pct"] = _pct(q.get("revenue_cr"), prev.get("revenue_cr"))
                q["pat_qoq_pct"] = _pct(q.get("pat_cr"), prev.get("pat_cr"))
            # Same quarter last year: match on a 330-400 day gap rather than
            # "four rows back", because filings have gaps.
            yago = next((o for o in rows[i + 1:]
                         if 330 <= (q["end"] - o["end"]).days <= 400), None)
            if yago:
                q["yoy_label"] = yago["label"]
                q["revenue_yoy_pct"] = _pct(q.get("revenue_cr"), yago.get("revenue_cr"))
                q["pat_yoy_pct"] = _pct(q.get("pat_cr"), yago.get("pat_cr"))

        shown = rows[:n]
        trend = _trend(shown)

        # How current is this really? The exchange feed can lag badly, and
        # presenting a filing as "the latest quarter" without saying how old it
        # is would be worse than not showing it — the whole reason to read
        # quarterly numbers instead of an annual report is recency.
        latest_end = shown[0]["end"] if shown else None
        age_days = (date.today() - latest_end).days if latest_end else None
        stale = age_days is not None and age_days > 150      # ~a quarter late

        for q in shown:
            q["end"] = q["end"].isoformat()

        return {
            "symbol": sym,
            "quarters": shown,
            "latest": shown[0] if shown else None,
            "latest_end": latest_end.isoformat() if latest_end else None,
            "latest_age_days": age_days,
            "stale": stale,
            "freshness": (
                f"Most recent filing available covers the quarter ending "
                f"{latest_end.isoformat()}, about {age_days // 30} months ago. "
                f"The exchange has published nothing newer, so anything since "
                f"then is not reflected here."
                if stale and latest_end else
                f"Latest filing covers the quarter ending {latest_end.isoformat()}."
                if latest_end else "No dated filings."),
            "basis": "consolidated" if series is cons else "standalone",
            "trend": trend,
            "source": "NSE quarterly result filings (XBRL)",
        }

    return get_or_set("quarterly_results", f"{sym}_{n}",
                      ttl_seconds=60 * 60 * 12, fn=_do) or {}


def _trend(rows: list[dict]) -> dict:
    """Plain reading of the direction, on year-on-year comparisons.

    Year-on-year rather than quarter-on-quarter: most Indian businesses have a
    seasonal shape, so a weak Q1 against a strong Q4 says nothing.
    """
    rev = [q.get("revenue_yoy_pct") for q in rows if q.get("revenue_yoy_pct") is not None]
    pat = [q.get("pat_yoy_pct") for q in rows if q.get("pat_yoy_pct") is not None]
    margins = [q.get("net_margin_pct") for q in rows if q.get("net_margin_pct") is not None]

    if not rev and not pat:
        return {"reading": "Not enough comparable quarters to call a trend.",
                "direction": "unknown"}

    bits = []
    direction = "mixed"
    if rev:
        avg = sum(rev) / len(rev)
        bits.append(f"revenue {'growing' if avg > 0 else 'shrinking'} "
                    f"{abs(avg):.0f}% year on year")
    if pat:
        avgp = sum(pat) / len(pat)
        bits.append(f"profit {'up' if avgp > 0 else 'down'} {abs(avgp):.0f}%")
        if rev:
            # Profit outpacing revenue is operating leverage; the reverse is
            # margin pressure, and that distinction is the whole point of
            # looking at several quarters instead of one.
            if avgp > sum(rev) / len(rev) + 3:
                bits.append("profit growing faster than sales — margins widening")
                direction = "improving"
            elif avgp < sum(rev) / len(rev) - 3:
                bits.append("profit lagging sales — margins under pressure")
                direction = "deteriorating"
            else:
                direction = "improving" if avgp > 0 else "deteriorating"
    if len(margins) >= 2:
        bits.append(f"net margin {margins[0]:.1f}% latest vs {margins[-1]:.1f}% "
                    f"{len(margins)} quarters ago")

    return {"direction": direction, "reading": "; ".join(bits).capitalize() + "."}

"""Comprehensive NSE India scraper.

NSE is the only data source reliably reachable from the user's network, so we
mine it hard:

  quote-equity            → P/E, sector, industry, 52-week range, last price
  corporates-financial-results → list of quarterly/annual filings; each filing
                            links an XBRL document containing the real numbers
  XBRL parse              → revenue, profit (PAT), EPS  → derived growth
  corporate-announcements → news / catalysts / red flags
  corporate-actions       → dividends, splits, bonuses

Every raw response can be dumped to .cache/nse_debug/<symbol>/ for inspection
(`debug_dump(symbol)` or `python main.py quant debug-nse SYMBOL`).

The session uses the proven cookie-warmup pattern (same as macro.py). NSE
rotates cookies aggressively, so calls auto-retry once with a fresh session.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests

from config import settings
from src.data.cache import get_or_set
from src.utils.logger import get_logger

log = get_logger("data.nse_scraper")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.nseindia.com/get-quotes/equity",
}

_SESSION: Optional[requests.Session] = None


def _session(force: bool = False) -> requests.Session:
    global _SESSION
    if _SESSION is not None and not force:
        return _SESSION
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get("https://www.nseindia.com", timeout=12)
        s.get("https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE", timeout=12)
    except Exception as e:
        log.debug(f"NSE warmup failed: {e}")
    _SESSION = s
    return s


def _get(path: str, want_json: bool = True, retries: int = 1):
    """GET an NSE API path. Returns parsed JSON (or raw text/bytes)."""
    s = _session()
    url = path if path.startswith("http") else f"https://www.nseindia.com{path}"
    for attempt in range(retries + 1):
        try:
            r = s.get(url, timeout=20)
            if r.status_code == 200:
                if not want_json:
                    return r.content
                try:
                    return r.json()
                except Exception:
                    return None
            if r.status_code in (401, 403) and attempt < retries:
                s = _session(force=True)
                continue
            log.debug(f"NSE {url} → {r.status_code}")
            return None
        except Exception as e:
            log.debug(f"NSE {url} attempt {attempt}: {e}")
            if attempt < retries:
                s = _session(force=True)
    return None


def _f(x) -> Optional[float]:
    if x in (None, "", "-", "NA", "N/A"):
        return None
    try:
        return float(str(x).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_date(s: Optional[str]) -> Optional[date]:
    """NSE dates come as '31-Mar-2024', '2024-03-31', '31-MAR-2024', etc.
    String-sorting these is WRONG ('Dec' < 'Mar'), so always parse first."""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # last resort: ISO-ish prefix
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            pass
    return None


# ────────────────────── XBRL financial-statement parser ──────────────────────
# Indian SEBI/MCA XBRL taxonomy uses predictable element local-names. We don't
# need a full XBRL engine — just pull the headline numbers by tag local-name,
# preferring the value attached to the most recent / largest-magnitude context.
_XBRL_TAGS = {
    "revenue": [
        "RevenueFromOperations", "Income", "TotalIncome",
        "RevenueFromOperationsNet", "TotalRevenueFromOperations",
    ],
    "profit": [
        "ProfitLossForPeriod", "ProfitLossForThePeriod",
        "ProfitLossAfterTaxFromOrdinaryActivities",
        "ProfitLossForPeriodFromContinuingOperations",
        "ComprehensiveIncomeForThePeriod",
    ],
    "eps": [
        "DilutedEarningsLossPerShareFromContinuingOperations",
        "BasicEarningsLossPerShareFromContinuingOperations",
        "DilutedEarningsPerShareAfterExtraordinaryItems",
        "BasicEarningsPerShareAfterExtraordinaryItems",
        "DilutedEarningsLossPerShare", "BasicEarningsLossPerShare",
    ],
}


def _parse_xbrl(xml_bytes: bytes) -> dict:
    """Context-aware numeric extraction from an NSE XBRL filing.

    The critical fix: an XBRL quarterly filing contains the SAME tag
    (e.g. RevenueFromOperations) multiple times — once per period context
    (the 3-month quarter, the 6/9/12-month cumulative, the prior-year quarter,
    etc.). Naively taking the largest value mixes a quarter against a
    year-to-date figure → nonsense growth like +102%.

    So we:
      1. parse every <context> → (start, end, duration_days)
      2. for each fact, look up its contextRef's period
      3. keep only facts whose period is a ~quarter (75-100 days)
      4. pick the one with the most recent end date

    Returns {revenue, profit, eps, period_end} for the quarter the filing
    actually reports.
    """
    try:
        text = xml_bytes.decode("utf-8", errors="replace")
    except Exception:
        return {}

    # 1. contexts: id → (start_date, end_date, duration_days)
    contexts: dict[str, tuple] = {}
    for m in re.finditer(
        r'<(?:[\w.-]+:)?context\s+id="([^"]+)"[^>]*>(.*?)</(?:[\w.-]+:)?context>',
        text, flags=re.DOTALL | re.IGNORECASE,
    ):
        cid, body = m.group(1), m.group(2)
        sd = re.search(r"<(?:[\w.-]+:)?startDate>\s*([\d-]+)", body)
        ed = re.search(r"<(?:[\w.-]+:)?endDate>\s*([\d-]+)", body)
        inst = re.search(r"<(?:[\w.-]+:)?instant>\s*([\d-]+)", body)
        try:
            if sd and ed:
                d0 = datetime.strptime(sd.group(1).strip(), "%Y-%m-%d").date()
                d1 = datetime.strptime(ed.group(1).strip(), "%Y-%m-%d").date()
                contexts[cid] = (d0, d1, (d1 - d0).days)
            elif inst:
                di = datetime.strptime(inst.group(1).strip(), "%Y-%m-%d").date()
                contexts[cid] = (di, di, 0)
        except ValueError:
            continue

    if not contexts:
        return {}

    out: dict = {}
    for key, tags in _XBRL_TAGS.items():
        # collect (end_date, duration, value) for every matching fact
        candidates: list[tuple] = []
        for tag in tags:
            for m in re.finditer(
                rf'<[\w.-]*:?{tag}\b[^>]*\bcontextRef="([^"]+)"[^>]*>([^<]+)'
                rf'</[\w.-]*:?{tag}>',
                text, flags=re.IGNORECASE,
            ):
                cref, raw = m.group(1), m.group(2)
                v = _f(raw)
                if v is None or cref not in contexts:
                    continue
                d0, d1, dur = contexts[cref]
                candidates.append((d1, dur, v))
        if not candidates:
            continue
        # prefer ~quarterly periods (75-100 days). EPS for a quarter is also
        # reported against the quarterly context.
        quarterly = [c for c in candidates if 75 <= c[1] <= 100]
        pool = quarterly or candidates
        # most recent end date wins
        pool.sort(key=lambda c: c[0], reverse=True)
        out[key] = pool[0][2]
        out.setdefault("period_end", pool[0][0].isoformat())
    return out


# ────────────────────── public scraper API ──────────────────────
def quote(symbol: str) -> dict:
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    j = _get(f"/api/quote-equity?symbol={sym}")
    if not j:
        return {}
    info = j.get("info", {}) or {}
    meta = j.get("metadata", {}) or {}
    price = j.get("priceInfo", {}) or {}
    ind = j.get("industryInfo", {}) or {}
    whl = price.get("weekHighLow", {}) or {}
    return {
        "source": "nse_quote",
        "pe": _f(meta.get("pdSymbolPe") or meta.get("symbolPe")),
        "sector_pe": _f(meta.get("pdSectorPe")),
        "sector": ind.get("sector") or ind.get("macro"),
        "industry": ind.get("industry") or ind.get("basicIndustry"),
        "company_name": info.get("companyName"),
        "current_price": _f(price.get("lastPrice")),
        "year_high": _f(whl.get("max")),
        "year_low": _f(whl.get("min")),
        "day_change_pct": _f(price.get("pChange")),
        "isin": info.get("isin"),
        "listing_date": meta.get("listingDate"),
    }


def financial_results(symbol: str, period: str = "Quarterly") -> list[dict]:
    """List of result filings. Each has the XBRL attachment URL we parse."""
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    j = _get(f"/api/corporates-financial-results"
             f"?index=equities&symbol={sym}&period={period}")
    rows = j if isinstance(j, list) else (j or {}).get("data", [])
    out = []
    for r in (rows or []):
        out.append({
            "from_date": r.get("fromDate") or r.get("re_from_date"),
            "to_date": r.get("toDate") or r.get("re_to_date"),
            "consolidated": "consolidat" in str(r.get("consolidated", "")).lower(),
            "audited": r.get("audited"),
            "xbrl_url": r.get("xbrl_attachment") or r.get("xbrl") or r.get("naXbrl"),
            "attachment": r.get("na_attachment") or r.get("attachmentFile"),
            "seq_id": r.get("seqId") or r.get("seq_id"),
            # some NSE responses DO embed the numbers directly:
            "income_inline": _f(r.get("income")),
            "profit_inline": _f(r.get("proLossAftTax") or r.get("profit_after_tax")),
            "eps_inline": _f(r.get("reDilEPS") or r.get("eps")),
        })
    return out


def derived_financials(symbol: str, max_xbrl: int = 6) -> dict:
    """Pull recent quarterly filings, parse XBRL, compute YoY growth.

    `max_xbrl` caps how many XBRL files we download per stock (each is a
    network round-trip). 6 = current quarter + ~5 back, enough for a clean
    year-over-year comparison.
    """
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")

    def _do():
        filings = financial_results(sym, "Quarterly")
        if not filings:
            return {}

        # Attach a real parsed date to every filing, drop undated ones.
        for f in filings:
            f["_end"] = _parse_date(f.get("to_date"))
        filings = [f for f in filings if f["_end"] is not None]
        if not filings:
            return {}

        # Prefer consolidated; sort by REAL date, newest first.
        cons = [f for f in filings if f["consolidated"]]
        series = cons if len(cons) >= 2 else filings
        series.sort(key=lambda f: f["_end"], reverse=True)

        quarters: list[dict] = []
        for f in series[:max_xbrl]:
            rev = f.get("income_inline")
            profit = f.get("profit_inline")
            eps = f.get("eps_inline")
            # Parse the XBRL attachment when the list didn't embed numbers
            if (rev is None or profit is None) and f.get("xbrl_url"):
                xml = _get(f["xbrl_url"], want_json=False)
                if xml:
                    p = _parse_xbrl(xml)
                    rev = rev if rev is not None else p.get("revenue")
                    profit = profit if profit is not None else p.get("profit")
                    eps = eps if eps is not None else p.get("eps")
            quarters.append({
                "end": f["_end"], "to_date": f.get("to_date"),
                "income": rev, "pat": profit, "eps": eps,
            })

        if not quarters:
            return {}
        latest = quarters[0]
        out = {
            "source": "nse_financials",
            "latest_quarter": latest["end"].isoformat(),
            "latest_eps": latest.get("eps"),
        }

        # YoY: find the quarter whose end date is ~365 days before `latest`
        # (330-400 day window). NOT just "index 4" — filings can have gaps.
        yago = None
        for q in quarters[1:]:
            gap = (latest["end"] - q["end"]).days
            if 330 <= gap <= 400:
                yago = q
                break

        def _growth(cur, prev):
            if cur is None or prev is None or prev == 0:
                return None
            g = round((cur - prev) / abs(prev) * 100, 2)
            # Sanity guard — a quarter growing >500% almost always means we
            # compared mismatched periods. Drop it rather than mislead.
            return g if -90 <= g <= 500 else None

        if yago:
            out["yoy_quarter"] = yago["end"].isoformat()
            sg = _growth(latest.get("income"), yago.get("income"))
            pg = _growth(latest.get("pat"), yago.get("pat"))
            if sg is not None:
                out["sales_growth_pct"] = sg
            if pg is not None:
                out["profit_growth_pct"] = pg

        # TTM EPS = sum of the 4 most recent distinct quarters
        eps_vals = [q["eps"] for q in quarters[:4] if q.get("eps") is not None]
        if len(eps_vals) >= 4:
            out["ttm_eps"] = round(sum(eps_vals), 2)

        return out if len(out) > 3 else out  # always return what we have

    return get_or_set("nse_derived_fin", sym, ttl_seconds=60 * 60 * 24, fn=_do) or {}


def announcements(symbol: str, limit: int = 12) -> list[dict]:
    """Corporate announcements — these double as reliable, India-native news."""
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")

    def _do():
        j = _get(f"/api/corporate-announcements?index=equities&symbol={sym}")
        rows = j if isinstance(j, list) else (j or {}).get("data", [])
        out = []
        for r in (rows or [])[:limit]:
            out.append({
                "title": r.get("subject") or r.get("desc") or r.get("attchmntText"),
                "date": r.get("an_dt") or r.get("sort_date") or r.get("date"),
                "snippet": (r.get("attchmntText") or r.get("smIndustry") or "")[:300],
                "source": "NSE corporate filing",
            })
        return out

    return get_or_set("nse_announcements", sym, ttl_seconds=60 * 60 * 6, fn=_do) or []


def corporate_actions(symbol: str) -> list[dict]:
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    j = _get(f"/api/corporates-corporateActions?index=equities&symbol={sym}")
    rows = j if isinstance(j, list) else (j or {}).get("data", [])
    return [{
        "subject": r.get("subject"),
        "ex_date": r.get("exDate"),
        "purpose": r.get("purpose"),
    } for r in (rows or [])[:10]]


# ────────────────────── diagnostics ──────────────────────
def debug_dump(symbol: str) -> dict:
    """Hit every NSE endpoint, save raw JSON to .cache/nse_debug/<symbol>/,
    and return a summary so we can see what NSE actually serves."""
    sym = symbol.upper()
    out_dir = settings.cache_dir / "nse_debug" / sym
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {"symbol": sym, "dump_dir": str(out_dir), "endpoints": {}}

    endpoints = {
        "quote_equity": f"/api/quote-equity?symbol={sym}",
        "financial_results_quarterly":
            f"/api/corporates-financial-results?index=equities&symbol={sym}&period=Quarterly",
        "financial_results_annual":
            f"/api/corporates-financial-results?index=equities&symbol={sym}&period=Annual",
        "announcements":
            f"/api/corporate-announcements?index=equities&symbol={sym}",
        "corporate_actions":
            f"/api/corporates-corporateActions?index=equities&symbol={sym}",
    }
    for name, path in endpoints.items():
        raw = _get(path)
        fp = out_dir / f"{name}.json"
        try:
            fp.write_text(json.dumps(raw, indent=2, default=str))
        except Exception:
            fp.write_text(str(raw)[:5000])
        # summarise
        if raw is None:
            summary["endpoints"][name] = "NULL (request failed / blocked)"
        elif isinstance(raw, list):
            summary["endpoints"][name] = (
                f"list of {len(raw)} — first item keys: "
                f"{list(raw[0].keys()) if raw else '[]'}")
        elif isinstance(raw, dict):
            summary["endpoints"][name] = f"dict — top keys: {list(raw.keys())}"
        else:
            summary["endpoints"][name] = f"{type(raw).__name__}"

    # parsed views
    summary["parsed_quote"] = quote(sym)
    summary["parsed_derived_financials"] = derived_financials(sym)
    summary["parsed_announcements_count"] = len(announcements(sym))
    return summary

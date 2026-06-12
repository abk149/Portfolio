"""NSE India — fundamentals + filings. Primary fundamentals source.

Why NSE first: it's the exchange itself, India-native, and not subject to the
network blocks / scraping fragility of screener.in. We already use NSE for
VIX / PCR so the cookie-warmup pattern is proven.

Endpoints used:
  /api/quote-equity?symbol=X
      → P/E, sector, industry, 52-week range, last price, company name
  /api/annual-reports?index=equities&symbol=X
      → list of annual-report PDF URLs (for KB ingestion later)
  /api/corporate-announcements?index=equities&symbol=X
      → recent corporate announcements (catalysts / red flags)

NSE rate-limits and rotates cookies aggressively, so every call goes through
a warmed Session and is wrapped in try/except — a miss just returns {}.
"""
from __future__ import annotations

from typing import Optional

import requests

from src.data.cache import get_or_set
from src.utils.logger import get_logger

log = get_logger("data.nse")

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.nseindia.com/get-quotes/equity",
}

_SESSION: Optional[requests.Session] = None


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    s = requests.Session()
    s.headers.update(NSE_HEADERS)
    # Warmup — NSE issues cookies on the homepage + a quote page
    try:
        s.get("https://www.nseindia.com", timeout=12)
        s.get("https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE", timeout=12)
    except Exception as e:
        log.debug(f"NSE session warmup failed (continuing): {e}")
    _SESSION = s
    return s


def _refresh_session() -> requests.Session:
    """NSE cookies expire fast; call this when a request 401/403s."""
    global _SESSION
    _SESSION = None
    return _session()


def _f(x) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(str(x).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _api_get(path: str, retries: int = 1) -> Optional[dict]:
    s = _session()
    url = f"https://www.nseindia.com{path}"
    for attempt in range(retries + 1):
        try:
            r = s.get(url, timeout=15)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (401, 403) and attempt < retries:
                log.debug(f"NSE {path} → {r.status_code}, refreshing session")
                s = _refresh_session()
                continue
            log.debug(f"NSE {path} → {r.status_code}")
            return None
        except Exception as e:
            log.debug(f"NSE {path} attempt {attempt} failed: {e}")
            if attempt < retries:
                s = _refresh_session()
    return None


# ---------- fundamentals ----------
def fetch_nse_fundamentals(symbol: str) -> dict:
    """P/E, sector, industry, 52-week range, last price — from NSE quote-equity."""
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")

    def _do():
        j = _api_get(f"/api/quote-equity?symbol={sym}")
        if not j:
            return {}
        info = j.get("info", {}) or {}
        metadata = j.get("metadata", {}) or {}
        price_info = j.get("priceInfo", {}) or {}
        industry_info = j.get("industryInfo", {}) or {}
        whl = price_info.get("weekHighLow", {}) or {}

        # NSE exposes P/E under metadata.pdSymbolPe (symbol P/E) — note it's a
        # string and can be "-" for loss-making companies.
        pe = _f(metadata.get("pdSymbolPe") or metadata.get("symbolPe"))
        sector_pe = _f(metadata.get("pdSectorPe"))

        out = {
            "source": "nse",
            "pe": pe,
            "sector_pe": sector_pe,
            "sector": industry_info.get("sector") or industry_info.get("macro"),
            "industry": industry_info.get("industry") or industry_info.get("basicIndustry"),
            "company_name": info.get("companyName"),
            "current_price": _f(price_info.get("lastPrice")),
            "year_high": _f(whl.get("max")),
            "year_low": _f(whl.get("min")),
            "day_change_pct": _f(price_info.get("pChange")),
            "face_value": _f(metadata.get("faceValue")),
            "isin": info.get("isin"),
            "listing_date": metadata.get("listingDate"),
        }
        # drop all-None payloads
        if not any(v is not None for k, v in out.items() if k != "source"):
            return {}
        return out

    return get_or_set("nse_fundamentals", sym, ttl_seconds=60 * 60 * 12, fn=_do) or {}


# ---------- annual report filings ----------
def fetch_annual_reports(symbol: str) -> list[dict]:
    """Return [{year, url, ...}] of annual-report PDFs published on NSE."""
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")

    def _do():
        j = _api_get(f"/api/annual-reports?index=equities&symbol={sym}")
        if not j:
            return []
        rows = j.get("data") or j if isinstance(j, list) else j.get("data", [])
        out = []
        for r in (rows or []):
            url = r.get("fileName") or r.get("attchmntFile") or r.get("url")
            if url and ".pdf" in str(url).lower():
                out.append({
                    "year": r.get("fromYr") or r.get("toYr") or r.get("year"),
                    "url": url if str(url).startswith("http")
                           else f"https://www.nseindia.com{url}",
                    "company": r.get("companyName"),
                })
        return out

    return get_or_set("nse_annual_reports", sym, ttl_seconds=60 * 60 * 24 * 7, fn=_do) or []


# ---------- quarterly financial results → derived growth metrics ----------
def fetch_nse_financials(symbol: str) -> dict:
    """Pull NSE's quarterly financial results and DERIVE growth metrics.

    NSE publishes every listed company's quarterly numbers. From the last
    several quarters we compute:
      - sales_growth_pct  (latest quarter income vs the same quarter a year ago)
      - profit_growth_pct (latest PAT vs year-ago PAT)
      - latest_eps, ttm_eps

    This is what lets us produce a real fundamental score even when Yahoo /
    screener.in are unreachable — NSE is the exchange, it always has this.
    """
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")

    def _do():
        # The list endpoint returns recent result filings with the actual
        # numbers embedded (income, expenditure, profit).
        j = _api_get(f"/api/corporates-financial-results"
                     f"?index=equities&symbol={sym}&period=Quarterly")
        rows = j if isinstance(j, list) else (j or {}).get("data", [])
        if not rows:
            return {}

        # Each row typically has: re_from_date / re_to_date, income, expenditure,
        # profit_before_tax (proLossBefTax), profit_after_tax (proLossAftTax),
        # diluted EPS (reDilEPS), audited, consolidated flag.
        parsed = []
        for r in rows:
            def _num(*keys):
                for k in keys:
                    v = r.get(k)
                    if v not in (None, "", "-"):
                        n = _f(v)
                        if n is not None:
                            return n
                return None
            parsed.append({
                "to_date": r.get("re_to_date") or r.get("toDate"),
                "from_date": r.get("re_from_date") or r.get("fromDate"),
                "income": _num("income", "totalIncome", "re_income"),
                "pat": _num("proLossAftTax", "profit_after_tax", "netProfit",
                            "re_profit_loss_aft_tax"),
                "eps": _num("reDilEPS", "diluted_eps", "reBasicEps", "eps"),
                "consolidated": str(r.get("consolidated", "")).lower() in ("yes", "true", "1"),
            })

        # Prefer consolidated; sort newest first by to_date
        def _key(p):
            return p.get("to_date") or ""
        cons = sorted([p for p in parsed if p["consolidated"]], key=_key, reverse=True)
        standalone = sorted([p for p in parsed if not p["consolidated"]], key=_key, reverse=True)
        series = cons if len(cons) >= 2 else standalone
        if len(series) < 1:
            return {}

        latest = series[0]
        out = {
            "source": "nse_financials",
            "latest_eps": latest.get("eps"),
            "latest_quarter": latest.get("to_date"),
        }
        # YoY: compare latest with the quarter ~4 entries back (same quarter, prior year)
        year_ago = series[4] if len(series) > 4 else (series[-1] if len(series) > 1 else None)
        if year_ago:
            if latest.get("income") and year_ago.get("income"):
                out["sales_growth_pct"] = round(
                    (latest["income"] - year_ago["income"]) / abs(year_ago["income"]) * 100, 2)
            if latest.get("pat") and year_ago.get("pat") and year_ago["pat"] != 0:
                out["profit_growth_pct"] = round(
                    (latest["pat"] - year_ago["pat"]) / abs(year_ago["pat"]) * 100, 2)
        # TTM EPS — sum of last 4 quarters if available
        eps_vals = [p["eps"] for p in series[:4] if p.get("eps") is not None]
        if len(eps_vals) >= 4:
            out["ttm_eps"] = round(sum(eps_vals), 2)
        return out if len(out) > 2 else {}

    return get_or_set("nse_financials", sym, ttl_seconds=60 * 60 * 24, fn=_do) or {}


# ---------- corporate announcements (catalysts / red flags) ----------
def fetch_announcements(symbol: str, limit: int = 10) -> list[dict]:
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")

    def _do():
        j = _api_get(f"/api/corporate-announcements?index=equities&symbol={sym}")
        rows = j if isinstance(j, list) else (j or {}).get("data", [])
        out = []
        for r in (rows or [])[:limit]:
            out.append({
                "subject": r.get("subject") or r.get("desc"),
                "date": r.get("an_dt") or r.get("date"),
                "detail": (r.get("attchmntText") or "")[:300],
            })
        return out

    return get_or_set("nse_announcements", sym, ttl_seconds=60 * 60 * 6, fn=_do) or []

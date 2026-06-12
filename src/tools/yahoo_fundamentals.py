"""Yahoo Finance fundamentals — direct API hit, no yfinance dependency.

Yahoo's `quoteSummary` endpoint returns clean JSON with P/E, ROE, D/E, growth,
sector, etc., for Indian stocks via the `.NS` (NSE) / `.BO` (BSE) suffix.

Their CDN (Akamai) is on a different network path than screener.in's AWS
Mumbai origin, so this works when screener.in is blocked.
"""
from __future__ import annotations

import time
from typing import Optional

import requests

from src.data.cache import get_or_set
from src.utils.logger import get_logger

log = get_logger("tools.yahoo")

HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]
MODULES = (
    "summaryDetail,defaultKeyStatistics,financialData,"
    "assetProfile,price,summaryProfile,quoteType"
)
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

_SESSION: Optional[requests.Session] = None
_CRUMB: Optional[str] = None


def _session(force: bool = False) -> requests.Session:
    """Yahoo's quoteSummary endpoint requires a cookie + crumb token since
    mid-2024. The handshake is:
      1. GET finance.yahoo.com           → sets the session cookies
      2. GET /v1/test/getcrumb           → returns the crumb string
      3. pass &crumb=<crumb> on every subsequent quoteSummary call
    """
    global _SESSION, _CRUMB
    if _SESSION is not None and not force:
        return _SESSION

    s = requests.Session()
    s.headers.update(UA)
    try:
        # 1. cookie warmup — a real page, not the API host
        s.get("https://finance.yahoo.com/", timeout=12)
        s.get("https://finance.yahoo.com/quote/RELIANCE.NS", timeout=12)
        # 2. crumb
        for host in HOSTS:
            try:
                cr = s.get(f"https://{host}/v1/test/getcrumb", timeout=10)
                txt = (cr.text or "").strip()
                # a valid crumb is short and not HTML
                if cr.status_code == 200 and txt and "<" not in txt and len(txt) < 40:
                    _CRUMB = txt
                    log.info(f"yahoo crumb acquired ({len(txt)} chars)")
                    break
            except Exception:
                continue
        if not _CRUMB:
            log.warning("yahoo: could not acquire crumb — quoteSummary may 401")
    except Exception as e:
        log.warning(f"yahoo session warmup failed: {e}")

    _SESSION = s
    return s


def _fetch_raw(ticker: str, _retry: bool = True) -> Optional[dict]:
    s = _session()
    params = {"modules": MODULES}
    if _CRUMB:
        params["crumb"] = _CRUMB
    for host in HOSTS:
        url = f"https://{host}/v10/finance/quoteSummary/{ticker}"
        try:
            r = s.get(url, params=params, timeout=15)
            if r.status_code == 401 and _retry:
                # crumb likely stale — re-handshake once
                log.debug(f"yahoo {ticker} 401, refreshing crumb")
                _session(force=True)
                return _fetch_raw(ticker, _retry=False)
            if r.status_code != 200:
                log.debug(f"yahoo {ticker} via {host} → {r.status_code}: {r.text[:200]}")
                continue
            j = r.json()
            res = (j.get("quoteSummary") or {}).get("result") or []
            if res:
                return res[0]
            # Yahoo sometimes returns an error block instead
            err = (j.get("quoteSummary") or {}).get("error")
            if err:
                log.debug(f"yahoo {ticker} error: {err}")
        except Exception as e:
            log.debug(f"yahoo {ticker} via {host} failed: {e}")
    return None


def _g(d: dict, *path):
    """Safe nested get with .raw unwrapping for Yahoo's verbose format."""
    cur = d
    for p in path:
        if cur is None or not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    if isinstance(cur, dict) and "raw" in cur:
        return cur["raw"]
    return cur


def _normalize(raw: dict) -> dict:
    """Adapt Yahoo's response into the same shape screener.in produces, so
    the rest of the pipeline doesn't have to care which source supplied data."""
    if not raw:
        return {}
    sd = raw.get("summaryDetail") or {}
    ks = raw.get("defaultKeyStatistics") or {}
    fd = raw.get("financialData") or {}
    ap = raw.get("assetProfile") or {}
    pr = raw.get("price") or {}

    mc_inr = _g(sd, "marketCap")
    roe = _g(fd, "returnOnEquity")          # decimal (0.18 = 18%)
    de = _g(fd, "debtToEquity")              # Yahoo expresses as %, e.g. 45.2
    eps = _g(ks, "trailingEps")
    rev_growth = _g(fd, "revenueGrowth")     # decimal
    eg = _g(fd, "earningsGrowth")            # decimal
    py = _g(sd, "yield")
    div_yield = _g(sd, "dividendYield")

    return {
        "source": "yahoo",
        "pe": _g(sd, "trailingPE") or _g(sd, "forwardPE"),
        "roe_pct": round(roe * 100, 2) if roe is not None else None,
        # Yahoo's debtToEquity is in % (a value of 45.2 means 0.45 ratio)
        "debt_to_equity": (de / 100) if de is not None else None,
        "book_value": _g(ks, "bookValue"),
        "market_cap_cr": (mc_inr / 1e7) if mc_inr else None,
        "dividend_yield_pct": (div_yield * 100) if div_yield is not None else (py * 100 if py is not None else None),
        "eps": eps,
        "sales_growth_pct": round(rev_growth * 100, 2) if rev_growth is not None else None,
        "profit_growth_pct": round(eg * 100, 2) if eg is not None else None,
        "current_price": _g(fd, "currentPrice") or _g(pr, "regularMarketPrice"),
        "year_high": _g(sd, "fiftyTwoWeekHigh"),
        "year_low": _g(sd, "fiftyTwoWeekLow"),
        "sector": ap.get("sector"),
        "industry": ap.get("industry"),
        "about": (ap.get("longBusinessSummary") or "")[:600],
        "long_name": pr.get("longName") or pr.get("shortName"),
    }


def fetch_fundamentals_yahoo(symbol: str, exchange: str = "NS") -> dict:
    """Public API. `symbol` should be the NSE/BSE trading symbol (no suffix).
    We try .NS first, then .BO."""
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    suffixes = [exchange] if exchange in ("NS", "BO") else ["NS", "BO"]
    if exchange == "NS":
        suffixes = ["NS", "BO"]    # try both even if asked for NS

    def _do():
        for suf in suffixes:
            ticker = f"{sym}.{suf}"
            raw = _fetch_raw(ticker)
            if raw:
                norm = _normalize(raw)
                if any(norm.get(k) is not None for k in ("pe", "roe_pct", "market_cap_cr")):
                    return norm
        return {}

    return get_or_set("yahoo_fundamentals", sym,
                      ttl_seconds=60 * 60 * 24, fn=_do) or {}

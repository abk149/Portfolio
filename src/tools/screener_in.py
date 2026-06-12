"""Scrape fundamentals from screener.in — the de-facto Indian retail FA site.

We pull:
  - Top-ratios: Market Cap, Stock P/E, Book Value, Dividend Yield, ROCE, ROE,
    Face Value, Debt to Equity, EPS, Sales / Profit Growth, Promoter Holding
  - Sector (from the breadcrumb)
  - Latest annual report URL (for downstream PDF analysis)

Cached for 24 hours on disk so a full Stage-2 scan doesn't hammer the site.

Robustness:
  - Uses a Session with a homepage warmup so we get the same cookies a real
    browser would.
  - Tries BOTH /company/{SYM}/ and /company/{SYM}/consolidated/.
  - Parser tries multiple CSS selectors (screener.in has tweaked its markup
    a few times — we want to survive minor variations).
  - When a page returns nothing parseable, we dump a 4 KB snippet of the HTML
    to .cache/screener_in_debug.html so you can inspect what we received.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config import settings
from src.data.cache import get_or_set
from src.utils.logger import get_logger

log = get_logger("tools.screener_in")

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Connection": "keep-alive",
}

_SESSION: Optional[requests.Session] = None
_DEBUG_PATH = settings.cache_dir / "screener_in_debug.html"


def _session() -> requests.Session:
    """One session per process. Warms up cookies by hitting the homepage."""
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    s = requests.Session()
    s.headers.update(UA)
    try:
        s.get("https://www.screener.in/", timeout=15)   # cookie warmup
    except Exception as e:
        log.debug(f"screener.in warmup failed (continuing): {e}")
    _SESSION = s
    return s


# ---------- value normaliser ----------
def _num(s: str) -> Optional[float]:
    if not s:
        return None
    # screener uses values like "₹ 25,000 Cr.", "20.4 %", "—", "0.45"
    s = s.strip().replace(" ", " ").replace(",", "")
    s = s.replace("₹", "").replace("%", "").strip()
    if s in ("", "-", "—", "N/A", "NA"):
        return None
    # strip any trailing unit (Cr., L., x, etc.)
    s = re.sub(r"[A-Za-z\.\s/]+$", "", s).strip()
    if not s or s in ("-", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ---------- fetch ----------
def _fetch_html(symbol: str, verbose: bool = False) -> tuple[Optional[str], dict]:
    """Return (html, diagnostics).  The diagnostics dict is for the debug
    endpoint so we can see exactly what screener.in is returning."""
    s = _session()
    diag = {"attempts": []}
    for path in ("/consolidated/", "/"):
        url = f"https://www.screener.in/company/{symbol.upper()}{path}"
        try:
            r = s.get(url, timeout=20, allow_redirects=True)
        except Exception as e:
            diag["attempts"].append({"url": url, "error": f"{type(e).__name__}: {e}"})
            continue
        info = {
            "url": url,
            "final_url": r.url,
            "status": r.status_code,
            "len": len(r.text),
            "has_top_ratios": "top-ratios" in r.text,
            "has_company_ratios": "company-ratios" in r.text,
            "looks_like_company_page": "<h1" in r.text and "Market Cap" in r.text,
        }
        diag["attempts"].append(info)
        # Accept ANY 200 with substantial HTML — don't gate on a specific marker
        if r.status_code == 200 and len(r.text) > 5000:
            if verbose:
                log.info(f"screener.in {symbol} → {r.status_code} "
                         f"len={len(r.text)} top-ratios={'top-ratios' in r.text}")
            return r.text, diag
    return None, diag


# ---------- canonical keys ----------
_KEY_REMAP = {
    "stock p/e": "pe", "p/e": "pe",
    "roe": "roe_pct", "return on equity": "roe_pct",
    "roce": "roce_pct", "return on capital employed": "roce_pct",
    "debt to equity": "debt_to_equity",
    "book value": "book_value",
    "market cap": "market_cap_cr",
    "dividend yield": "dividend_yield_pct",
    "face value": "face_value",
    "current price": "current_price",
    "high / low": "year_high_low",
    "eps": "eps",
    "sales growth": "sales_growth_pct",
    "profit growth": "profit_growth_pct",
    "promoter holding": "promoter_holding_pct",
}


# ---------- parse ----------
def _slug(key: str) -> str:
    return (key.replace(" ", "_").replace(".", "")
            .replace("/", "_to_").replace("%", "pct"))


def _parse(html: str, debug_symbol: str = "") -> dict:
    """Try multiple CSS strategies — screener.in has changed its markup
    every year or so. Falls back to a regex pass on the raw HTML if
    nothing structured matches."""
    soup = BeautifulSoup(html, "lxml")
    out: dict = {}

    # Strategy 1: ul#top-ratios → <li> blocks (current markup)
    items = soup.select("ul#top-ratios li") or \
            soup.select("#top-ratios li") or \
            soup.select(".company-ratios li") or \
            soup.select(".top-ratios li")

    for li in items:
        # The label is the .name span; the value can be inside .number,
        # .value > .number, or just a bare text node after .name.
        name_el = li.select_one(".name") or li.select_one(".label")
        if not name_el:
            continue
        key_raw = name_el.get_text(" ", strip=True).lower()
        if not key_raw:
            continue

        # Try several places for the numeric value
        val_str = None
        num_el = li.select_one(".number")
        if num_el:
            val_str = num_el.get_text(" ", strip=True)
        else:
            val_el = li.select_one(".nowrap.value") or li.select_one(".value")
            if val_el:
                val_str = val_el.get_text(" ", strip=True)
        # Last resort: take everything after the label text
        if not val_str:
            full = li.get_text(" ", strip=True)
            if full.lower().startswith(key_raw):
                val_str = full[len(key_raw):].strip()

        if val_str is None:
            continue

        out_key = _KEY_REMAP.get(key_raw, _slug(key_raw))
        out[out_key] = _num(val_str)

    # Strategy 2 (fallback): regex on the raw HTML for the common labels
    if not out:
        log.debug(f"screener.in {debug_symbol}: structured parse empty, "
                  f"trying regex fallback")
        for label, canonical in [
            (r"Stock P/?E", "pe"),
            (r"Market Cap", "market_cap_cr"),
            (r"Book Value", "book_value"),
            (r"Dividend Yield", "dividend_yield_pct"),
            (r"ROCE", "roce_pct"),
            (r"ROE", "roe_pct"),
            (r"Debt to Equity", "debt_to_equity"),
            (r"Face Value", "face_value"),
            (r"EPS", "eps"),
            (r"Sales Growth", "sales_growth_pct"),
            (r"Profit Growth", "profit_growth_pct"),
        ]:
            m = re.search(
                rf"{label}.{{0,200}}?(?:<span[^>]*class=\"number\"[^>]*>|>)"
                rf"\s*([₹\d.,\s/%-]+)",
                html, flags=re.IGNORECASE | re.DOTALL,
            )
            if m:
                out[canonical] = _num(m.group(1))

    # Sector — screener.in puts citation links like "[1]" inside the company
    # description, and our old selector grabbed those. Look specifically for
    # an <a> whose href points at a sector/industry page, and reject anything
    # that looks like a citation marker.
    sector_val = None
    for a in soup.select(".company-info a, .company-profile a, [itemprop='industry']"):
        href = (a.get("href") or "").lower()
        txt = a.get_text(strip=True)
        if not txt or re.fullmatch(r"\[?\d+\]?", txt):   # skip "[1]", "2", etc.
            continue
        if "/company/compare/" in href or "sector" in href or "industry" in href:
            sector_val = txt
            break
    if sector_val:
        out["sector"] = sector_val

    # About (short description)
    about = (soup.select_one(".company-profile .sub p")
             or soup.select_one(".about p")
             or soup.select_one(".company-info p"))
    if about:
        out["about"] = about.get_text(" ", strip=True)[:600]

    # Document links (annual reports / concalls)
    docs: list[dict] = []
    for a in soup.select("section#documents a, .documents a, .annual-reports a"):
        href = a.get("href", "")
        if href and ".pdf" in href.lower():
            docs.append({
                "title": a.get_text(" ", strip=True),
                "url": href if href.startswith("http") else f"https://www.screener.in{href}",
            })
    if docs:
        out["recent_documents"] = docs[:8]

    # If we extracted nothing, dump a snippet for debugging
    if not any(out.get(k) is not None for k in
               ("pe", "roe_pct", "debt_to_equity", "market_cap_cr")) and html:
        try:
            _DEBUG_PATH.write_text(html[:4000])
            log.warning(
                f"screener.in {debug_symbol}: parser found 0 ratios. "
                f"First 4 KB of the response dumped to {_DEBUG_PATH.name} "
                f"so you can inspect what their page looked like."
            )
        except Exception:
            pass

    return out


# ---------- public API ----------
def _screener_only(symbol: str) -> dict:
    """Just the screener.in scrape, cached."""
    def _from_screener():
        html, _diag = _fetch_html(symbol)
        if not html:
            return {}
        try:
            return _parse(html, debug_symbol=symbol)
        except Exception as e:
            log.warning(f"screener.in {symbol} parse error: {e}")
            return {}
    return get_or_set("screener_in", symbol.upper(),
                      ttl_seconds=60 * 60 * 24, fn=_from_screener) or {}


def fetch_fundamentals(symbol: str) -> dict:
    """Multi-source merge — fills each field from the best available source.

    Priority per field:
      1. NSE India     — P/E, sector, industry, 52w range, last price  (reliable)
      2. Yahoo Finance — ROE, D/E, growth, market cap                  (good coverage)
      3. screener.in   — everything, if the network can reach it       (richest)

    Returns canonical keys: pe, roe_pct, debt_to_equity, sales_growth_pct,
    profit_growth_pct, market_cap_cr, sector, industry, year_high/low, etc.
    `_sources` lists which providers actually contributed.
    """
    merged: dict = {}
    sources_used: list[str] = []

    def _absorb(src_name: str, data: dict):
        if not data:
            return
        added = False
        for k, v in data.items():
            if v is None or k in ("source",):
                continue
            if merged.get(k) is None:        # first non-null wins
                merged[k] = v
                added = True
        if added:
            sources_used.append(src_name)

    # 1. NSE quote — primary: P/E, sector, industry, 52w range, price
    try:
        from src.data.nse_fundamentals import fetch_nse_fundamentals
        _absorb("nse", fetch_nse_fundamentals(symbol))
    except Exception as e:
        log.debug(f"NSE fundamentals {symbol}: {e}")

    # 2. NSE quarterly financials — derived sales / profit growth + EPS,
    #    parsed from the XBRL filings NSE publishes. NSE is the exchange, it
    #    always has these, so we get real growth metrics even when Yahoo /
    #    screener.in are unreachable.
    try:
        from src.data.nse_scraper import derived_financials
        _absorb("nse_financials", derived_financials(symbol))
    except Exception as e:
        log.debug(f"NSE derived financials {symbol}: {e}")

    # 3. stockanalysis.com — by far the most reliable free source for full
    #    fundamentals (PE, ROE, ROA, D/E, current ratio, growth, margins).
    #    Clean server-rendered HTML, no auth, no JS, not ISP-blocked.
    try:
        from src.tools.stockanalysis import fetch_stockanalysis
        _absorb("stockanalysis", fetch_stockanalysis(symbol))
    except Exception as e:
        log.debug(f"stockanalysis {symbol}: {e}")

    # 4. Tickertape — JSON API, India-native, covers virtually every NSE name
    try:
        from src.tools.tickertape import fetch_tickertape
        _absorb("tickertape", fetch_tickertape(symbol))
    except Exception as e:
        log.debug(f"tickertape {symbol}: {e}")

    # 5. Groww — extracts data from their Next.js JSON blob
    try:
        from src.tools.groww import fetch_groww
        _absorb("groww", fetch_groww(symbol))
    except Exception as e:
        log.debug(f"groww {symbol}: {e}")

    # 6. MoneyControl — autosuggest + page scrape; gives extra ratios + news
    try:
        from src.tools.moneycontrol import fetch_moneycontrol
        mc = fetch_moneycontrol(symbol)
        if mc:
            adapted = {
                "pe": mc.get("p_to_e") or mc.get("pe"),
                "book_value": mc.get("book_value"),
                "dividend_yield_pct": mc.get("dividend_yield"),
                "market_cap_cr": mc.get("market_cap"),
                "mc_news": mc.get("mc_news"),
                "mc_url": mc.get("mc_url"),
            }
            _absorb("moneycontrol", adapted)
    except Exception as e:
        log.debug(f"MoneyControl {symbol}: {e}")

    # 7. Yahoo — last-resort backup (often crumb-blocked, won't hurt to try)
    try:
        from src.tools.yahoo_fundamentals import fetch_fundamentals_yahoo
        _absorb("yahoo", fetch_fundamentals_yahoo(symbol))
    except Exception as e:
        log.debug(f"Yahoo fundamentals {symbol}: {e}")

    # 8. screener.in — only bother if we're STILL missing key ratios
    still_missing = any(merged.get(k) is None
                        for k in ("roe_pct", "debt_to_equity"))
    if still_missing:
        try:
            _absorb("screener_in", _screener_only(symbol))
        except Exception as e:
            log.debug(f"screener.in {symbol}: {e}")

    merged["_sources"] = sources_used
    return merged


# ---------- live debug helper (called from /api/debug/screener) ----------
def debug_fetch(symbol: str) -> dict:
    """Live fetch + parse, no cache. Returns diagnostics so we can SEE what's
    happening when fundamentals aren't extracting."""
    import gzip
    from pathlib import Path
    sym = symbol.upper()
    html, diag = _fetch_html(sym, verbose=True)
    out = {"symbol": sym, "fetch": diag, "parsed": {}}
    if not html:
        out["error"] = "fetch returned no HTML"
        return out
    # Save full HTML (gzipped) for offline inspection
    dump_path = settings.cache_dir / f"screener_debug_{sym}.html.gz"
    try:
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(dump_path, "wb") as fh:
            fh.write(html.encode("utf-8"))
        out["full_html_saved"] = str(dump_path)
    except Exception as e:
        out["full_html_save_error"] = str(e)
    # Inline preview of likely-relevant fragment
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.select_one("h1")
    out["h1"] = (h1.get_text(strip=True) if h1 else "(no <h1> found)")
    tr = (soup.select_one("ul#top-ratios")
          or soup.select_one("#top-ratios")
          or soup.select_one(".company-ratios")
          or soup.select_one(".top-ratios"))
    out["found_ratio_container"] = tr is not None
    if tr:
        out["ratio_container_snippet"] = str(tr)[:2000]
    else:
        # Grab the part of the HTML around "Market Cap" so we can see what
        # the markup actually looks like
        idx = html.lower().find("market cap")
        if idx >= 0:
            out["market_cap_context"] = html[max(0, idx - 200): idx + 600]
        else:
            out["html_start"] = html[:1500]
    out["parsed"] = _parse(html, debug_symbol=sym)
    return out


def annual_report_url(symbol: str) -> Optional[str]:
    data = fetch_fundamentals(symbol)
    for doc in data.get("recent_documents", []):
        title = (doc.get("title") or "").lower()
        if "annual" in title and ".pdf" in doc.get("url", "").lower():
            return doc["url"]
    return None

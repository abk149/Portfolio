"""stockanalysis.com — the most reliable free fundamentals source for
Indian equities. Clean server-rendered HTML tables, no auth, no JS.

URL patterns:
  https://stockanalysis.com/quote/nse/<SYMBOL>/             (overview)
  https://stockanalysis.com/quote/nse/<SYMBOL>/statistics/  (full ratios)
  https://stockanalysis.com/quote/bse/<SYMBOL>/             (BSE fallback)

What we extract:
  pe, forward_pe, pb, roe_pct, roa_pct, debt_to_equity, current_ratio,
  sales_growth_pct, profit_growth_pct, market_cap_cr, dividend_yield_pct,
  eps, profit_margin_pct, sector, industry.
"""
from __future__ import annotations

import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

from src.data.cache import get_or_set
from src.utils.logger import get_logger

log = get_logger("tools.stockanalysis")

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _f(s: str) -> Optional[float]:
    if not s:
        return None
    t = s.strip().replace(",", "").replace("$", "").replace("₹", "")
    # Handle suffixes: B (billion), M (million), K (thousand), T, % stripped
    mult = 1.0
    if t.endswith("%"):
        t = t[:-1].strip()
    if t and t[-1] in "BMKT":
        suf = t[-1]
        mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[suf]
        t = t[:-1].strip()
    if t in ("", "-", "--", "N/A", "NA", "n/a"):
        return None
    try:
        return float(t) * mult
    except ValueError:
        return None


# Map stockanalysis.com label text → our canonical key
_KEY_MAP = {
    # Valuation
    "pe ratio":                "pe",
    "p/e ratio":               "pe",
    "trailing p/e":            "pe",
    "forward p/e":             "forward_pe",
    "p/b ratio":               "pb",
    "price/book":              "pb",
    "price/sales":             "ps",
    "ev/ebitda":               "ev_ebitda",
    # Profitability
    "return on equity":        "roe_pct",
    "return on equity (roe)":  "roe_pct",
    "roe":                     "roe_pct",
    "return on assets":        "roa_pct",
    "roa":                     "roa_pct",
    "profit margin":           "profit_margin_pct",
    "operating margin":        "operating_margin_pct",
    # Financial position
    "debt / equity":           "debt_to_equity",
    "debt/equity":             "debt_to_equity",
    "total debt/equity":       "debt_to_equity",
    "current ratio":           "current_ratio",
    "quick ratio":             "quick_ratio",
    # Growth
    "revenue growth":          "sales_growth_pct",
    "revenue growth (yoy)":    "sales_growth_pct",
    "sales growth":            "sales_growth_pct",
    "earnings growth":         "profit_growth_pct",
    "net income growth":       "profit_growth_pct",
    "eps growth":              "eps_growth_pct",
    # Other
    "market cap":              "market_cap_raw",
    "dividend yield":          "dividend_yield_pct",
    "eps (ttm)":               "eps",
    "eps":                     "eps",
    "book value":              "book_value",
    "shares outstanding":      "shares_outstanding_raw",
}


def _fetch_html(symbol: str) -> tuple[str, str]:
    """Returns (html, exchange) — tries NSE first, then BSE. ('','') if both fail."""
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    s = requests.Session()
    s.headers.update(UA)
    for ex in ("nse", "bse"):
        for path in ("/statistics/", "/financials/ratios/", "/"):
            url = f"https://stockanalysis.com/quote/{ex}/{sym}{path}"
            try:
                r = s.get(url, timeout=15)
                if r.status_code == 200 and len(r.text) > 5000 and \
                   "Page not found" not in r.text:
                    return r.text, ex
            except Exception as e:
                log.debug(f"stockanalysis {url[:60]}: {e}")
    return "", ""


def _parse(html: str) -> dict:
    """Walk every <table> row; left cell = label, right cell = value."""
    out: dict = {}
    if not html:
        return out
    soup = BeautifulSoup(html, "lxml")

    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            label = cells[0].get_text(" ", strip=True).lower()
            value = cells[-1].get_text(" ", strip=True)
            key = _KEY_MAP.get(label)
            if not key:
                # Fuzzy match — handle slight label variations
                for k_lbl, k_canon in _KEY_MAP.items():
                    if k_lbl == label or label.startswith(k_lbl + " "):
                        key = k_canon
                        break
            if not key or key in out:
                continue
            num = _f(value)
            if num is None:
                continue
            # Convert raw market cap to crores (stockanalysis often uses B/T)
            if key == "market_cap_raw":
                out["market_cap_cr"] = round(num / 1e7, 1)   # rupees → crores
            elif key == "shares_outstanding_raw":
                out["shares_outstanding_mn"] = round(num / 1e6, 1)
            else:
                out[key] = num

    # Sector / industry — usually in a meta tag or breadcrumb
    for selector in ('meta[name="description"]', 'meta[property="og:description"]'):
        m = soup.select_one(selector)
        if m and m.get("content"):
            desc = m["content"]
            sm = re.search(r"sector[:\s]+([A-Za-z &]+)", desc, flags=re.IGNORECASE)
            if sm and "sector" not in out:
                out["sector"] = sm.group(1).strip()
            break

    return out


def fetch_stockanalysis(symbol: str) -> dict:
    """Public API. Returns canonical-keyed dict; {} on miss."""
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")

    def _do():
        html, ex = _fetch_html(sym)
        if not html:
            return {}
        data = _parse(html)
        if data:
            data["source"] = f"stockanalysis ({ex})"
            data["sa_url"] = f"https://stockanalysis.com/quote/{ex}/{sym}/"
        return data

    return get_or_set("stockanalysis", sym, ttl_seconds=60 * 60 * 24, fn=_do) or {}

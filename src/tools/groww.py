"""Groww.in — Indian retail brokerage with public stock pages.

Their stock pages embed structured JSON for the company in a Next.js
__NEXT_DATA__ <script> blob. We pull that out and read fundamentals
directly — much more reliable than HTML scraping.

URL pattern:
  https://groww.in/stocks/<slug>
where slug is e.g. 'reliance-industries-ltd' — we resolve it via their
search API.
"""
from __future__ import annotations

import json
import re
from typing import Optional

import requests

from src.data.cache import get_or_set
from src.utils.logger import get_logger

log = get_logger("tools.groww")

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}


def _resolve_slug(symbol: str) -> Optional[str]:
    """Use Groww's autocomplete to map an NSE symbol → page slug."""
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    try:
        r = requests.get(
            "https://groww.in/v1/api/search/v1/derived/stocks",
            params={"q": sym, "page": 0, "size": 5},
            headers=UA, timeout=10,
        )
        if r.status_code == 200:
            items = (r.json() or {}).get("data", []) or []
            for it in items:
                # Match the NSE ticker exactly when possible
                if (it.get("nse_scrip_code") or "").upper() == sym:
                    return it.get("search_id") or it.get("growwSlug")
            if items:                                # fallback to first hit
                return items[0].get("search_id") or items[0].get("growwSlug")
    except Exception as e:
        log.debug(f"groww search {sym}: {e}")
    return None


def _extract_next_data(html: str) -> Optional[dict]:
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html, flags=re.DOTALL,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def _walk(obj, hits: dict):
    """Recursively walk Groww's JSON looking for known fundamental keys."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = k.lower()
            if isinstance(v, (int, float)):
                if kl in ("pe", "peratio", "trailingpe"):
                    hits.setdefault("pe", float(v))
                elif kl in ("pbratio", "pricetobook"):
                    hits.setdefault("pb", float(v))
                elif kl in ("roe", "returnonequity"):
                    hits.setdefault("roe_pct",
                                    float(v) * 100 if abs(v) <= 5 else float(v))
                elif kl in ("debttoequity", "totaldebtbyequity"):
                    hits.setdefault("debt_to_equity", float(v))
                elif kl in ("eps", "epsbasic", "epsdilluted", "epsdiluted"):
                    hits.setdefault("eps", float(v))
                elif kl in ("dividendyield",):
                    hits.setdefault("dividend_yield_pct",
                                    float(v) * 100 if abs(v) <= 1 else float(v))
                elif kl in ("marketcap", "mcap"):
                    hits.setdefault("market_cap_cr", float(v) / 1e7)
                elif kl in ("bookvalue", "bookvaluepershare"):
                    hits.setdefault("book_value", float(v))
                elif "salesgrowth" in kl or "revenuegrowth" in kl:
                    hits.setdefault("sales_growth_pct",
                                    float(v) * 100 if abs(v) <= 5 else float(v))
                elif "profitgrowth" in kl or "earningsgrowth" in kl:
                    hits.setdefault("profit_growth_pct",
                                    float(v) * 100 if abs(v) <= 5 else float(v))
            elif isinstance(v, str) and kl == "sector":
                hits.setdefault("sector", v)
            elif isinstance(v, str) and kl == "industry":
                hits.setdefault("industry", v)
            _walk(v, hits)
    elif isinstance(obj, list):
        for x in obj:
            _walk(x, hits)


def fetch_groww(symbol: str) -> dict:
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")

    def _do():
        slug = _resolve_slug(sym)
        if not slug:
            return {}
        url = f"https://groww.in/stocks/{slug}"
        try:
            r = requests.get(url, headers=UA, timeout=15)
            if r.status_code != 200 or len(r.text) < 5000:
                return {}
        except Exception as e:
            log.debug(f"groww fetch {url}: {e}")
            return {}
        data = _extract_next_data(r.text)
        if not data:
            return {}
        hits: dict = {}
        _walk(data, hits)
        if hits:
            hits["source"] = "groww"
            hits["groww_url"] = url
        return hits

    return get_or_set("groww", sym, ttl_seconds=60 * 60 * 24, fn=_do) or {}

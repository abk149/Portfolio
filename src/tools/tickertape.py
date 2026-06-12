"""Tickertape.in — Indian-equities platform with rich fundamentals.

Tickertape has both a JSON API and Next.js-rendered pages. The JSON path is
preferred (cleaner, faster). We try:
  GET https://api.tickertape.in/stocks/RELI                  (id resolve)
  GET https://api.tickertape.in/stocks/<sid>/info            (company info)
  GET https://api.tickertape.in/stocks/<sid>/scorecard       (rating + ratios)
"""
from __future__ import annotations

import re
from typing import Optional

import requests

from src.data.cache import get_or_set
from src.utils.logger import get_logger

log = get_logger("tools.tickertape")

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,*/*",
    "Origin": "https://www.tickertape.in",
    "Referer": "https://www.tickertape.in/",
}


def _api_get(path: str) -> Optional[dict]:
    try:
        r = requests.get(f"https://api.tickertape.in{path}",
                         headers=UA, timeout=12)
        if r.status_code == 200:
            return r.json()
        log.debug(f"tickertape {path} → {r.status_code}")
    except Exception as e:
        log.debug(f"tickertape {path}: {e}")
    return None


def _resolve_sid(symbol: str) -> Optional[str]:
    """Map NSE symbol → Tickertape sid (e.g. 'RELI' for Reliance)."""
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    # The search endpoint
    j = _api_get(f"/search?text={sym}&types=stock&pageNumber=0")
    if not j:
        return None
    hits = (j.get("data") or {}).get("stocks") or j.get("data") or []
    for h in hits:
        if isinstance(h, dict):
            if (h.get("ticker") or "").upper() == sym:
                return h.get("sid")
    if hits and isinstance(hits[0], dict):
        return hits[0].get("sid")
    return None


def _f(v) -> Optional[float]:
    if v in (None, "", "-", "N/A"):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def fetch_tickertape(symbol: str) -> dict:
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")

    def _do():
        sid = _resolve_sid(sym)
        if not sid:
            return {}
        info = _api_get(f"/stocks/{sid}/info") or {}
        scorecard = _api_get(f"/stocks/{sid}/scorecard") or {}

        # Both endpoints can have differing shapes; we just hunt for known keys
        out: dict = {"source": "tickertape", "tt_sid": sid}

        def _walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    kl = k.lower()
                    if isinstance(v, (int, float)):
                        if kl in ("pe", "peratio"):
                            out.setdefault("pe", float(v))
                        elif kl in ("pb", "pbratio"):
                            out.setdefault("pb", float(v))
                        elif kl in ("roe", "returnonequity"):
                            val = float(v)
                            out.setdefault("roe_pct",
                                           val * 100 if abs(val) <= 5 else val)
                        elif kl in ("de", "debttoequity"):
                            out.setdefault("debt_to_equity", float(v))
                        elif kl in ("eps",):
                            out.setdefault("eps", float(v))
                        elif kl in ("mcap", "marketcap"):
                            out.setdefault("market_cap_cr", float(v) / 1e7)
                        elif kl in ("dividendyield", "divyield"):
                            val = float(v)
                            out.setdefault("dividend_yield_pct",
                                           val * 100 if abs(val) <= 1 else val)
                        elif "salesgrowth" in kl or "revenuegrowth" in kl:
                            val = float(v)
                            out.setdefault("sales_growth_pct",
                                           val * 100 if abs(val) <= 5 else val)
                        elif "profitgrowth" in kl:
                            val = float(v)
                            out.setdefault("profit_growth_pct",
                                           val * 100 if abs(val) <= 5 else val)
                    elif isinstance(v, str):
                        if kl == "sector":
                            out.setdefault("sector", v)
                        elif kl == "industry":
                            out.setdefault("industry", v)
                    _walk(v)
            elif isinstance(o, list):
                for x in o:
                    _walk(x)

        _walk(info)
        _walk(scorecard)
        if not any(out.get(k) is not None
                   for k in ("pe", "roe_pct", "debt_to_equity")):
            return {}
        return out

    return get_or_set("tickertape", sym, ttl_seconds=60 * 60 * 24, fn=_do) or {}

"""MoneyControl deep scraper.

MoneyControl is the largest Indian-equities portal — for almost every NSE
stock it has:
  • a stock-price page          (overview, key ratios, sector P/E)
  • a financials page           (annual P&L summary)
  • a news page                 (curated article list)
  • a balance-sheet page

URLs are per-company and unpredictable (`/stockpricequote/<sector>/<co>/SYM`),
so we use their autosuggest API to find the right one, then scrape the linked
sub-pages.

All requests are best-effort — if MC blocks us this run, the rest of the
funnel still works.
"""
from __future__ import annotations

import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

from src.data.cache import get_or_set
from src.utils.logger import get_logger

log = get_logger("tools.moneycontrol")

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}


def _autosuggest(query: str) -> Optional[dict]:
    """Find a stock on MoneyControl. Returns {'name','url','sc_id'} or None."""
    try:
        r = requests.get(
            "https://www.moneycontrol.com/mccode/common/autosuggestion_solr.php",
            params={"classic": "true", "query": query, "type": "1",
                    "format": "json"},
            headers=UA, timeout=12,
        )
        if r.status_code != 200:
            return None
        items = r.json() if r.headers.get("content-type", "").startswith(
            "application/json") else []
        for it in items:
            if not isinstance(it, dict):
                continue
            url = it.get("link_src") or it.get("link")
            if not url:
                continue
            if not url.startswith("http"):
                url = "https://www.moneycontrol.com" + url
            return {"name": it.get("pdt_dis_nm") or it.get("name"),
                    "url": url,
                    "sc_id": it.get("sc_id"),
                    "stock_name": it.get("stock_name")}
    except Exception as e:
        log.debug(f"mc autosuggest '{query}': {e}")
    return None


def _fetch(url: str) -> str:
    try:
        r = requests.get(url, headers=UA, timeout=15)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        log.debug(f"mc fetch {url[:60]}: {e}")
    return ""


def _parse_overview(html: str) -> dict:
    out = {}
    soup = BeautifulSoup(html, "lxml")
    # MC's overview key-ratios block — selectors change occasionally; try a few
    for sel in (
        "ul#nseind_indicator li, ul#bseind_indicator li",
        ".oview_table tr",
        ".keyrato_tbl tr",
    ):
        for li in soup.select(sel):
            txt = li.get_text(" ", strip=True)
            # patterns like "P/E 24.3" or "EPS (TTM) 56.02"
            m = re.match(r"(.+?)\s+([₹\d.,-]+)\s*$", txt)
            if not m:
                continue
            label, val = m.group(1).strip(), m.group(2).strip()
            key = (label.lower().replace(" ", "_")
                   .replace("/", "_to_").replace("(", "").replace(")", ""))
            try:
                out[key] = float(val.replace(",", "").replace("₹", ""))
            except ValueError:
                pass
    # plain "key" → "value" tables
    for tr in soup.select("table tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) == 2 and cells[0] and cells[1]:
            key = cells[0].lower().replace(" ", "_").replace("/", "_to_")
            v = cells[1].replace(",", "").replace("₹", "").strip()
            try:
                out.setdefault(key, float(v))
            except ValueError:
                pass
    return out


def fetch_moneycontrol(symbol: str, company_name: Optional[str] = None) -> dict:
    """Top-level: resolve MC URL, pull overview ratios, list recent news links."""
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    q = company_name or sym

    def _do():
        target = _autosuggest(q) or _autosuggest(sym)
        if not target:
            log.debug(f"mc: no autosuggest match for {sym}/{q}")
            return {}
        url = target["url"]
        html = _fetch(url)
        if not html:
            return {"mc_url": url, "_error": "fetch failed"}
        out = {
            "source": "moneycontrol",
            "mc_url": url,
            "mc_name": target.get("name"),
            "sc_id": target.get("sc_id"),
        }
        out.update(_parse_overview(html))

        # also pull the news listing snippet
        soup = BeautifulSoup(html, "lxml")
        news = []
        for a in soup.select("a"):
            href = a.get("href", "")
            txt = a.get_text(strip=True)
            if not txt or len(txt) < 20:
                continue
            if "/news/" in href and href.endswith(".html"):
                full = href if href.startswith("http") \
                    else f"https://www.moneycontrol.com{href}"
                news.append({"title": txt[:160], "url": full,
                             "source": "MoneyControl"})
            if len(news) >= 8:
                break
        if news:
            out["mc_news"] = news
        return out

    return get_or_set("moneycontrol", sym,
                      ttl_seconds=60 * 60 * 12, fn=_do) or {}

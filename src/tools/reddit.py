"""Reddit scraper — public JSON API, no auth required for read.

Indian-investing subreddits give you genuine retail discussion that doesn't
show up in mainstream news: tip-offs, anecdotes, complaints, technical chart
posts. The signal/noise ratio is mixed but useful for sentiment.
"""
from __future__ import annotations

from typing import Optional

import requests

from src.data.cache import get_or_set
from src.utils.logger import get_logger

log = get_logger("tools.reddit")

UA = {"User-Agent": "D-R1-Quant/1.0 (personal-research)"}

# India-focused subs first, generic last (those are noisier)
SUBS = ("IndianStockMarket", "IndiaInvestments", "DalalStreetTalks",
        "IndianStreetBets", "StockMarketIndia", "stocks")


def _do_search(query: str, subreddit: Optional[str], limit: int,
               sort: str = "new") -> list[dict]:
    if subreddit:
        url = f"https://www.reddit.com/r/{subreddit}/search.json"
        params = {"q": query, "restrict_sr": "on", "sort": sort, "limit": limit, "t": "year"}
    else:
        url = "https://www.reddit.com/search.json"
        params = {"q": query, "sort": sort, "limit": limit, "t": "year"}
    try:
        r = requests.get(url, headers=UA, params=params, timeout=15)
        if r.status_code != 200:
            log.debug(f"reddit {url} → {r.status_code}")
            return []
        children = r.json().get("data", {}).get("children", []) or []
        out = []
        for c in children:
            d = c.get("data", {})
            out.append({
                "title": d.get("title"),
                "url": "https://reddit.com" + (d.get("permalink") or ""),
                "subreddit": d.get("subreddit"),
                "score": d.get("score", 0),
                "num_comments": d.get("num_comments", 0),
                "created_utc": d.get("created_utc"),
                "snippet": (d.get("selftext") or "")[:600],
                "author": d.get("author"),
            })
        return out
    except Exception as e:
        log.debug(f"reddit search failed q='{query}' sub={subreddit}: {e}")
        return []


def reddit_for_symbol(symbol: str, company_name: Optional[str] = None,
                      per_sub: int = 4) -> list[dict]:
    """Pull recent posts mentioning a stock across the India-focused subs.
    Dedupes by URL, sorts by upvote score, returns up to ~15."""
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    q = f"{sym}" + (f' OR "{company_name}"' if company_name else "")
    cache_key = f"{sym}_{(company_name or '')[:30]}"

    def _do():
        posts: list[dict] = []
        for sub in SUBS[:4]:                              # cap to 4 subs per call
            posts.extend(_do_search(q, sub, per_sub))
        # dedupe by URL, then rank by score
        seen, ranked = set(), []
        for p in sorted(posts, key=lambda x: x.get("score", 0) or 0, reverse=True):
            if p.get("url") and p["url"] not in seen:
                seen.add(p["url"])
                ranked.append(p)
        return ranked[:15]

    return get_or_set("reddit", cache_key, ttl_seconds=60 * 60 * 6, fn=_do) or []

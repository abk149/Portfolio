"""Google News RSS feed — broad, reliable, no auth.

`https://news.google.com/rss/search?q=<query>` returns a structured XML
feed of recent articles from every news source Google indexes — order of
magnitude more articles than DuckDuckGo's `site:` filter alone.
"""
from __future__ import annotations

import re
from typing import Optional

import requests

from src.data.cache import get_or_set
from src.utils.logger import get_logger

log = get_logger("tools.gnews")

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def google_news_rss(query: str, limit: int = 12,
                    hl: str = "en-IN", gl: str = "IN") -> list[dict]:
    """Return [{title, url, source, snippet, published}] from Google News."""
    if not query:
        return []
    q_enc = requests.utils.quote(query)
    url = (f"https://news.google.com/rss/search?q={q_enc}"
           f"&hl={hl}&gl={gl}&ceid={gl}:{hl.split('-')[0]}")

    def _do():
        try:
            r = requests.get(url, headers=UA, timeout=15)
            if r.status_code != 200:
                log.debug(f"gnews {query} → {r.status_code}")
                return []
            text = r.text
        except Exception as e:
            log.debug(f"gnews fetch failed: {e}")
            return []
        items = []
        for m in re.finditer(r"<item>(.*?)</item>", text, flags=re.DOTALL):
            block = m.group(1)
            def _grab(tag: str) -> str:
                mm = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>",
                               block, flags=re.DOTALL)
                return (mm.group(1).strip() if mm else "")
            title = re.sub(r"<[^>]+>", "", _grab("title"))
            link = _grab("link")
            desc = re.sub(r"<[^>]+>", " ", _grab("description"))
            source = _grab("source")
            pub = _grab("pubDate")
            if title and link:
                items.append({
                    "title": title,
                    "url": link,
                    "source": f"Google News [{source}]" if source else "Google News",
                    "snippet": re.sub(r"\s+", " ", desc).strip()[:400],
                    "published": pub,
                })
            if len(items) >= limit:
                break
        return items

    return get_or_set("google_news",
                      re.sub(r"\W+", "_", query.lower())[:64],
                      ttl_seconds=60 * 30, fn=_do) or []

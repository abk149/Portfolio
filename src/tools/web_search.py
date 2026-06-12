"""Multi-source news aggregator.

Each source is independent and best-effort — one source going dark never
breaks the others. Sources combined:

  1. DuckDuckGo  → indian-outlet-filtered articles (moneycontrol / ET / mint /
                   business-standard / hindubusinessline)
  2. Reddit      → r/IndianStockMarket, r/IndiaInvestments, r/DalalStreetTalks
  3. NSE filings → corporate announcements (these double as authoritative news)

`news_for()` returns title+snippet+source. `news_with_bodies()` additionally
fetches the article body for the top URLs — gives the LLM real paragraphs
instead of headline snippets.

Every result is auto-indexed into the KB (semantic search later).
"""
from __future__ import annotations

import time
from typing import Optional

from src.utils.logger import get_logger

log = get_logger("tools.web")

SITE_FILTER = ("(site:moneycontrol.com OR site:economictimes.indiatimes.com "
               "OR site:livemint.com OR site:business-standard.com "
               "OR site:thehindubusinessline.com OR site:ndtvprofit.com)")


class WebSearcher:
    def __init__(self, vector_store=None, max_results: int = 5):
        self.max_results = max_results
        self.vec = vector_store

    # ---------- individual sources ----------
    def _ddg(self, query: str, india_only: bool = True) -> list[dict]:
        try:
            try:
                from ddgs import DDGS                       # new name
            except ImportError:
                from duckduckgo_search import DDGS          # legacy
            q = f"{query} {SITE_FILTER}" if india_only else query
            with DDGS() as ddgs:
                rows = list(ddgs.text(q, max_results=self.max_results))
        except Exception as e:
            log.debug(f"DDG search failed: {e}")
            return []
        out = []
        for r in rows or []:
            out.append({
                "source": "DuckDuckGo",
                "title": r.get("title", ""),
                "snippet": r.get("body", "")[:400],
                "url": r.get("href") or r.get("url"),
                "fetched_at": int(time.time()),
            })
        return out

    def _reddit(self, stock: str, company_name: Optional[str]) -> list[dict]:
        try:
            from src.tools.reddit import reddit_for_symbol
            posts = reddit_for_symbol(stock, company_name)
        except Exception as e:
            log.debug(f"reddit fetch failed for {stock}: {e}")
            return []
        out = []
        for p in posts[:6]:
            out.append({
                "source": f"Reddit /r/{p.get('subreddit', '?')}",
                "title": p.get("title"),
                "snippet": (p.get("snippet") or "")[:400],
                "url": p.get("url"),
                "score": p.get("score"),
                "comments": p.get("num_comments"),
            })
        return out

    def _nse_filings(self, stock: str) -> list[dict]:
        try:
            from src.data.nse_scraper import announcements
            anns = announcements(stock, limit=6)
        except Exception as e:
            log.debug(f"NSE announcements failed for {stock}: {e}")
            return []
        out = []
        for a in anns or []:
            title = a.get("title") or a.get("subject") or "(no title)"
            out.append({
                "source": "NSE corporate filing",
                "title": title,
                "snippet": (a.get("detail") or a.get("snippet") or "")[:400],
                "date": a.get("date"),
                "url": None,
            })
        return out

    def _google_news(self, stock: str, company_name: Optional[str]) -> list[dict]:
        try:
            from src.tools.google_news import google_news_rss
            q = f"{company_name or stock} stock"
            return google_news_rss(q, limit=8)
        except Exception as e:
            log.debug(f"Google News {stock} failed: {e}")
            return []

    def _moneycontrol_news(self, stock: str, company_name: Optional[str]) -> list[dict]:
        try:
            from src.tools.moneycontrol import fetch_moneycontrol
            mc = fetch_moneycontrol(stock, company_name)
            return mc.get("mc_news") or []
        except Exception as e:
            log.debug(f"MC news {stock} failed: {e}")
            return []

    # ---------- public ----------
    def news_for(self, stock_name: str, company_name: Optional[str] = None) -> list[dict]:
        """Aggregated news — DDG + Google News + Reddit + MoneyControl +
        NSE filings. Each source is independent; one failing never breaks
        the others."""
        out: list[dict] = []
        out.extend(self._ddg(f"{stock_name} stock news"))
        out.extend(self._google_news(stock_name, company_name))
        out.extend(self._reddit(stock_name, company_name))
        out.extend(self._moneycontrol_news(stock_name, company_name))
        out.extend(self._nse_filings(stock_name))

        # dedupe by URL where URLs exist
        seen, deduped = set(), []
        for item in out:
            key = item.get("url") or (item.get("title", ""), item.get("source"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        # persist into the KB so future queries can semantic-search them
        if self.vec and deduped:
            try:
                self.vec.add_news(stock_name, deduped)
            except Exception as e:
                log.debug(f"vector add failed: {e}")

        counts_by_source = {}
        for it in deduped:
            counts_by_source[it["source"]] = counts_by_source.get(it["source"], 0) + 1
        log.info(f"  news for {stock_name}: {len(deduped)} items "
                 f"({counts_by_source})")
        return deduped

    def news_with_bodies(self, stock_name: str,
                         company_name: Optional[str] = None,
                         body_limit: int = 6) -> list[dict]:
        """Like news_for, but also fetches the FULL article body for the top
        `body_limit` outbound URLs — JSON-LD extraction handles modern
        news sites (even JS-heavy ones), falling back to BeautifulSoup
        article-container heuristics."""
        items = self.news_for(stock_name, company_name)
        from src.tools.article_fetcher import fetch_article_body
        n_fetched = 0
        n_tried = 0
        for it in items:
            if n_fetched >= body_limit:
                break
            url = it.get("url")
            if not url or it.get("body"):
                continue
            if "reddit.com" in url:        # snippet is already the post body
                continue
            n_tried += 1
            body = fetch_article_body(url)
            if body:
                it["body"] = body
                n_fetched += 1
        log.info(f"  ↳ fetched bodies: {n_fetched}/{n_tried} articles")
        return items

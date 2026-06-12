"""Fetch and extract the body text of a news article URL.

DuckDuckGo gives us ~200-char snippets. The model needs the actual paragraphs
to reason about events. We do best-effort scraping with BeautifulSoup +
common article selectors, cache aggressively, and never raise — a failed
fetch just returns "" so the calling code keeps moving.
"""
from __future__ import annotations

import hashlib
import re

import requests
from bs4 import BeautifulSoup

from src.data.cache import get_or_set
from src.utils.logger import get_logger

log = get_logger("tools.article")

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

# Selectors commonly used by Indian financial news sites (moneycontrol,
# ET, livemint, business-standard, hindubusinessline, ndtvprofit).
_ARTICLE_SELECTORS = (
    "[itemprop='articleBody']",
    "article",
    "div.article-body, div.articleBody, div.article_body",
    "div.story-content, div.story-body",
    "div.contentSection",
    "section.article-text",
    "div#articleText",
    "div.detail-content, div.story_para",
    "main",
)


def fetch_article_body(url: str, max_chars: int = 4000) -> str:
    """Return article text (≤max_chars) or "" if extraction failed."""
    if not url:
        return ""
    key = hashlib.sha1(url.encode()).hexdigest()[:20]

    def _do():
        try:
            r = requests.get(url, headers=UA, timeout=15)
            if r.status_code != 200 or not r.text:
                return ""

            # Strategy 1: JSON-LD structured data (modern news sites embed
            # the full article body here — works for moneycontrol, ET,
            # livemint, business-standard, ndtvprofit, etc., even when
            # their pages are JS-heavy).
            import json
            for m in re.finditer(
                r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                r.text, flags=re.DOTALL | re.IGNORECASE,
            ):
                try:
                    blob = json.loads(m.group(1).strip())
                except Exception:
                    continue
                # could be a single dict, a list, or a graph
                items = blob if isinstance(blob, list) else \
                        (blob.get("@graph", []) + [blob])
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    typ = it.get("@type", "")
                    if isinstance(typ, list):
                        typ = ",".join(typ)
                    if "Article" in str(typ) or "NewsArticle" in str(typ):
                        body = it.get("articleBody") or it.get("description")
                        if body and len(body) > 300:
                            return re.sub(r"\s+", " ", body)[:max_chars]

            soup = BeautifulSoup(r.text, "lxml")
            for tag in soup(["script", "style", "nav", "aside", "footer",
                             "header", "form", "iframe", "noscript"]):
                tag.decompose()

            # Strategy 2: og:description meta tag — short but always there
            og_desc = ""
            og = soup.find("meta", property="og:description") or \
                 soup.find("meta", attrs={"name": "description"})
            if og and og.get("content"):
                og_desc = og["content"]

            # Strategy 3: known article containers, in order
            for sel in _ARTICLE_SELECTORS:
                el = soup.select_one(sel)
                if el:
                    txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
                    if len(txt) > 300:
                        return txt[:max_chars]

            # Strategy 4: concatenate every <p>
            paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
            txt = re.sub(r"\s+", " ", " ".join(paras))
            if len(txt) > 300:
                return txt[:max_chars]

            # Strategy 5: at minimum return og:description
            return og_desc[:max_chars] if og_desc else ""
        except Exception as e:
            log.debug(f"article fetch {url[:60]}: {e}")
            return ""

    return get_or_set("articles", key, ttl_seconds=60 * 60 * 24 * 3, fn=_do) or ""

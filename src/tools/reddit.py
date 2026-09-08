"""Reddit reader — deliberately cautious, and never trusted on its own.

Two hard realities shape this module.

**Reddit blocks us.** `www.reddit.com/*.json` now returns 403 to any
non-browser User-Agent, and even the paths that do work rate-limit to roughly
one request per cooldown window from a single IP — the second call in a burst
comes back 429 with an empty body. So this module:

  * uses the Atom feeds (`/r/<sub>/new.rss`), which still serve with a browser
    UA, instead of the dead `.json` endpoints;
  * serialises requests behind a process-wide throttle and honours Retry-After;
  * trips a circuit breaker after repeated failures so a blocked Reddit costs
    one timeout, not one per subreddit on every call;
  * caches each subreddit for hours, and rotates which sub it refreshes.

**Reddit is not evidence.** It is anonymous, unverifiable and frequently wrong
or promotional. Nothing from here should reach a decision on its own — callers
are expected to run these posts through `src.tools.corroborate`, which keeps
only claims independently confirmed by a real news source. `fetch_verified()`
does that for you and is the function you almost always want.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from src.data.cache import get_or_set
from src.utils.logger import get_logger

log = get_logger("tools.reddit")

# Reddit 403s the polite custom UA the old code used.
UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/atom+xml,application/xml,text/xml,*/*",
}

SUBS = ("IndianStockMarket", "IndianStreetBets", "IndiaInvestments",
        "DalalStreetTalks", "StockMarketIndia", "NSEbets")

_MIN_INTERVAL_S = 6.0          # between any two Reddit requests
_COOLDOWN_S = 300.0            # after a 429 / repeated failure
_BREAKER_THRESHOLD = 3         # consecutive failures before the breaker opens
_CACHE_TTL_S = 60 * 60 * 4     # a sub's feed is good for 4h


class _Gate:
    """Process-wide rate gate + circuit breaker for Reddit."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._blocked_until = 0.0
        self._failures = 0
        self.last_error = ""

    @property
    def open(self) -> bool:
        """True when the breaker is tripped and we should not call Reddit."""
        return time.time() < self._blocked_until

    def wait_turn(self, budget_s: float) -> bool:
        """Block until a request is allowed. False if that would exceed budget."""
        with self._lock:
            if self.open:
                return False
            gap = _MIN_INTERVAL_S - (time.time() - self._last_request)
            if gap > budget_s:
                return False
            if gap > 0:
                time.sleep(gap)
            self._last_request = time.time()
            return True

    def ok(self) -> None:
        with self._lock:
            self._failures = 0
            self.last_error = ""

    def fail(self, error: str, retry_after: Optional[float] = None) -> None:
        with self._lock:
            self._failures += 1
            self.last_error = error
            if retry_after or self._failures >= _BREAKER_THRESHOLD:
                self._blocked_until = time.time() + (retry_after or _COOLDOWN_S)
                log.info(f"reddit breaker open for "
                         f"{int(self._blocked_until - time.time())}s ({error})")

    def status(self) -> dict:
        return {
            "available": not self.open,
            "consecutive_failures": self._failures,
            "cooldown_remaining_s": max(0, int(self._blocked_until - time.time())),
            "last_error": self.last_error,
        }


_GATE = _Gate()


def health() -> dict:
    """Diagnostics for the UI — is Reddit reachable right now, and why not?"""
    st = _GATE.status()
    st["note"] = (
        "Reddit rate-limits hard from a single IP; posts are cached for hours "
        "and every post is cross-checked against real news before use."
        if st["available"] else
        "Reddit is rate-limiting or blocking us. This is expected and harmless "
        "— it is an optional sentiment source, never a decision input."
    )
    return st


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
def _parse_atom(body: str, sub: str) -> list[dict]:
    import html
    out: list[dict] = []
    for chunk in re.split(r"<entry>", body)[1:]:
        def grab(tag: str) -> str:
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", chunk, re.S)
            if not m:
                return ""
            v = re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))
            txt = html.unescape(re.sub(r"<[^>]+>", " ", v))
            # Reddit's Atom <content> is HTML wrapping the post plus a
            # "submitted by /u/x [link] [comments]" footer. Left in, its URLs
            # and markup words became matchable tokens — a post once
            # "corroborated" a headline on the shared phrase "href https".
            txt = re.sub(r"https?://\S+|www\.\S+", " ", txt)
            txt = re.sub(r"\bsubmitted by\b.*$", " ", txt, flags=re.I | re.S)
            return re.sub(r"\s+", " ", txt).strip()

        title = grab("title")
        if not title:
            continue
        link = re.search(r'<link[^>]+href="([^"]+)"', chunk)
        updated = grab("updated") or grab("published")
        created = None
        try:
            created = datetime.fromisoformat(
                updated.replace("Z", "+00:00")).timestamp() if updated else None
        except Exception:
            created = None
        out.append({
            "title": title[:240],
            "url": link.group(1) if link else "",
            "subreddit": sub,
            "author": grab("name"),
            "created_utc": created,
            "published": updated,
            "snippet": grab("content")[:600],
            # Reddit's Atom feed carries no score; absence is honest here
            # rather than a fabricated 0 that looks like a real measurement.
            "score": None,
            "num_comments": None,
        })
    return out


def _fetch_sub_uncached(sub: str, limit: int, budget_s: float) -> list[dict]:
    if not _GATE.wait_turn(budget_s):
        log.debug(f"reddit gate closed, skipping r/{sub}")
        return []
    url = f"https://www.reddit.com/r/{sub}/new.rss?limit={limit}"
    try:
        r = requests.get(url, headers=UA, timeout=12)
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            _GATE.fail("HTTP 429 (rate limited)",
                       retry_after=float(ra) if ra and ra.isdigit() else _COOLDOWN_S)
            return []
        if r.status_code != 200 or not r.content:
            _GATE.fail(f"HTTP {r.status_code}")
            return []
        posts = _parse_atom(r.content.decode("utf-8", "replace"), sub)
        if posts:
            _GATE.ok()
        else:
            _GATE.fail("empty feed")
        return posts
    except Exception as e:
        _GATE.fail(f"{type(e).__name__}: {e}"[:100])
        return []


def subreddit_posts(sub: str, limit: int = 25, budget_s: float = 8.0) -> list[dict]:
    """Recent posts from one subreddit. Cached for hours; [] if unavailable."""
    return get_or_set("reddit_sub", f"{sub}_{limit}", ttl_seconds=_CACHE_TTL_S,
                      fn=lambda: _fetch_sub_uncached(sub, limit, budget_s)) or []


def recent_posts(subs: Optional[tuple[str, ...]] = None, per_sub: int = 25,
                 total_budget_s: float = 20.0) -> list[dict]:
    """Recent posts across the India subs, within a wall-clock budget.

    Serialised on purpose: Reddit rejects bursts, so parallelism here would
    turn one usable response into six 429s.
    """
    if _GATE.open:
        return []
    deadline = time.time() + total_budget_s
    out: list[dict] = []
    for sub in (subs or SUBS):
        remaining = deadline - time.time()
        if remaining <= 0 or _GATE.open:
            break
        out.extend(subreddit_posts(sub, per_sub, budget_s=remaining))
    return out


def _matches(post: dict, terms: list[str]) -> bool:
    hay = f"{post.get('title', '')} {post.get('snippet', '')}".lower()
    return any(t in hay for t in terms if t)


def _do_search(query: str, subreddit: Optional[str], limit: int,
               sort: str = "new") -> list[dict]:
    """Compatibility shim for the old search API.

    Reddit's search endpoints are 403/429 for us, so this pulls the subreddit's
    recent feed (cached) and filters locally. Same shape out; far fewer requests.
    """
    terms = [t.strip().lower() for t in re.split(r"\s+OR\s+|\s+", query or "")
             if len(t.strip()) > 2 and t.strip().upper() != "OR"]
    posts = subreddit_posts(subreddit, 25) if subreddit else recent_posts()
    hits = [p for p in posts if not terms or _matches(p, terms)]
    return hits[:limit]


def reddit_for_symbol(symbol: str, company_name: Optional[str] = None,
                      per_sub: int = 4) -> list[dict]:
    """Recent posts mentioning a stock. Unverified — see `fetch_verified`."""
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    terms = [sym.lower()] + ([company_name.lower()] if company_name else [])
    posts = recent_posts()
    hits = [p for p in posts if _matches(p, terms)]
    seen, out = set(), []
    for p in hits:
        if p.get("url") and p["url"] not in seen:
            seen.add(p["url"])
            out.append(p)
    return out[:15]


# ---------------------------------------------------------------------------
# The function callers should actually use
# ---------------------------------------------------------------------------
def fetch_verified(news_items: Optional[list[dict]] = None,
                   limit: int = 20, total_budget_s: float = 20.0) -> dict:
    """Reddit chatter, filtered down to posts corroborated by real news.

    An anonymous post is a lead, not a fact. This keeps only posts whose
    substance is independently confirmed by a non-Reddit source, and reports
    how many were discarded so the drop is visible rather than silent.
    """
    from src.tools.corroborate import corroborate

    posts = recent_posts(total_budget_s=total_budget_s)
    if news_items is None:
        try:
            from src.tools.news_sources import fetch_all
            news_items = fetch_all(limit_per_source=6, days=7)["items"]
        except Exception as e:
            log.debug(f"news pool for corroboration failed: {e}")
            news_items = []

    result = corroborate(posts, news_items)
    result["verified"] = result["verified"][:limit]
    result["reddit_health"] = health()
    return result

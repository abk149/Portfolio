"""The news backbone: many Indian-market feeds, fetched robustly.

Design goals, in order:

  1. **Never fail the caller.** Every fetch is wrapped; a dead source shrinks
     the result set, it never raises and never blocks. If literally everything
     fails you get an empty list plus a health report saying why — not an
     exception four call-frames up.
  2. **Breadth.** One outlet going down (or blocking us) must not blind the
     system, so the list is deliberately wide and spans wires, exchanges and
     regulators as well as newspapers.
  3. **Honesty about what worked.** `fetch_all` returns a per-source health
     report. A source that quietly returns zero items is worse than one that
     errors, because it looks like "no news" rather than "no feed".

Every URL here was verified live before being added. Feeds that answer 200 with
an HTML page (Financial Express) or block us outright (Zeebiz) are routed via
Google News instead of being listed as direct feeds and silently yielding
nothing, which is what the previous list did.
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import requests

from src.utils.logger import get_logger

log = get_logger("tools.news_sources")

# A real browser UA. Several of these hosts 403 anything that looks automated.
UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,*/*",
    "Accept-Language": "en-IN,en;q=0.9",
}


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    region: str = "India"          # India | Global
    category: str = "markets"      # markets | economy | companies | regulator | wire
    weight: float = 1.0            # corroboration trust weight


# ---------------------------------------------------------------------------
# Direct RSS/Atom feeds — all verified returning parseable items.
# ---------------------------------------------------------------------------
FEEDS: list[Source] = [
    # ── Newspapers / business dailies ──
    Source("Economic Times Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    Source("Economic Times Stocks", "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms"),
    Source("ET Economy", "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms", category="economy"),
    Source("Moneycontrol Markets", "https://www.moneycontrol.com/rss/marketreports.xml"),
    Source("Moneycontrol Business", "https://www.moneycontrol.com/rss/business.xml", category="companies"),
    Source("Moneycontrol Results", "https://www.moneycontrol.com/rss/results.xml", category="companies"),
    Source("Moneycontrol Economy", "https://www.moneycontrol.com/rss/economy.xml", category="economy"),
    Source("Moneycontrol Latest", "https://www.moneycontrol.com/rss/latestnews.xml"),
    Source("LiveMint Markets", "https://www.livemint.com/rss/markets"),
    Source("LiveMint Economy", "https://www.livemint.com/rss/economy", category="economy"),
    Source("LiveMint Companies", "https://www.livemint.com/rss/companies", category="companies"),
    Source("LiveMint Money", "https://www.livemint.com/rss/money"),
    Source("LiveMint Industry", "https://www.livemint.com/rss/industry", category="companies"),
    Source("Business Standard Markets", "https://www.business-standard.com/rss/markets-106.rss"),
    Source("Business Standard Economy", "https://www.business-standard.com/rss/economy-102.rss", category="economy"),
    Source("Business Standard Companies", "https://www.business-standard.com/rss/companies-101.rss", category="companies"),
    Source("BusinessLine Markets", "https://www.thehindubusinessline.com/markets/feeder/default.rss"),
    Source("BusinessLine Economy", "https://www.thehindubusinessline.com/economy/feeder/default.rss", category="economy"),
    Source("BusinessLine Companies", "https://www.thehindubusinessline.com/companies/feeder/default.rss", category="companies"),
    Source("The Hindu Business", "https://www.thehindu.com/business/feeder/default.rss"),
    Source("Times of India Business", "https://timesofindia.indiatimes.com/rssfeeds/1898055.cms"),
    Source("NDTV Profit", "https://feeds.feedburner.com/ndtvprofit-latest"),
    Source("CNBC-TV18 Market", "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml"),
    Source("CNBC-TV18 Economy", "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/economy.xml", category="economy"),
    Source("Investing.com India", "https://in.investing.com/rss/news_25.rss"),
    Source("Investing.com Economy", "https://in.investing.com/rss/news_14.rss", category="economy"),

    # ── Primary sources: regulators + exchanges. Highest trust for
    #    corroboration — these are the entities that actually make the news.
    Source("SEBI", "https://www.sebi.gov.in/sebirss.xml", category="regulator", weight=1.6),
    Source("RBI", "https://www.rbi.org.in/pressreleases_rss.xml", category="regulator", weight=1.6),
    Source("PIB (Govt of India)", "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3",
           category="regulator", weight=1.5),
    Source("BSE announcements", "https://www.bseindia.com/data/xml/notices.xml",
           category="regulator", weight=1.5),
    Source("NSE circulars", "https://nsearchives.nseindia.com/content/RSS/Circulars.xml",
           category="regulator", weight=1.5),

    # ── Global, for the forces that drive FII flows into India ──
    Source("Yahoo Finance", "https://finance.yahoo.com/news/rssindex",
           region="Global", category="wire"),
]

# Outlets whose own feeds are unusable (HTML behind an RSS URL, or a 403) but
# which Google News indexes fine. Routed rather than dropped.
GNEWS_SITE_QUERIES: list[tuple[str, str]] = [
    ("Financial Express", "site:financialexpress.com market OR economy"),
    ("Zee Business", "site:zeebiz.com market"),
    ("Reuters India", "site:reuters.com india markets OR economy"),
    ("Bloomberg India", "site:bloomberg.com india markets"),
]

# Broad topic sweeps — catch anything the fixed feeds miss.
GNEWS_TOPICS: list[str] = [
    "Nifty Sensex today",
    "FII DII flows Indian equities",
    "RBI repo rate policy",
    "India CPI inflation",
    "USDINR rupee outlook",
    "crude oil price India impact",
    "US Fed rate decision India markets",
    "India quarterly results earnings",
    "SEBI regulation market",
    "India GDP growth",
]


# ---------------------------------------------------------------------------
# Health tracking — persists for the life of the process
# ---------------------------------------------------------------------------
@dataclass
class SourceHealth:
    name: str
    ok: bool = False
    items: int = 0
    ms: int = 0
    error: str = ""
    consecutive_failures: int = 0
    last_ok: Optional[str] = None


_HEALTH: dict[str, SourceHealth] = {}
_HEALTH_LOCK = threading.Lock()


def _record(name: str, ok: bool, items: int, ms: int, error: str = "") -> None:
    with _HEALTH_LOCK:
        h = _HEALTH.setdefault(name, SourceHealth(name=name))
        h.ok, h.items, h.ms, h.error = ok, items, ms, error
        if ok:
            h.consecutive_failures = 0
            h.last_ok = datetime.now().isoformat(timespec="minutes")
        else:
            h.consecutive_failures += 1


def health_report() -> dict:
    """Per-source status from the most recent fetch. Drives the UI diagnostics."""
    with _HEALTH_LOCK:
        rows = [
            {"name": h.name, "ok": h.ok, "items": h.items, "ms": h.ms,
             "error": h.error, "consecutive_failures": h.consecutive_failures,
             "last_ok": h.last_ok}
            for h in sorted(_HEALTH.values(), key=lambda x: (x.ok, -x.items))
        ]
    return {
        "sources": rows,
        "total": len(rows),
        "healthy": sum(1 for r in rows if r["ok"]),
        "degraded": [r["name"] for r in rows if not r["ok"]],
    }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
_TAG = re.compile(r"<[^>]+>")
_CDATA = re.compile(r"<!\[CDATA\[|\]\]>")


def _clean(v: str) -> str:
    import html
    return html.unescape(_TAG.sub(" ", _CDATA.sub("", v or ""))).strip()


def parse_feed(body: str, limit: int) -> list[dict]:
    """RSS <item> and Atom <entry>, one parser. Best-effort and total."""
    chunks = re.split(r"<item[\s>]|<entry[\s>]", body)[1:]
    out: list[dict] = []
    for chunk in chunks[: limit * 3]:
        def grab(tag: str) -> str:
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", chunk, re.S | re.I)
            return _clean(m.group(1)) if m else ""

        title = grab("title")
        if not title:
            continue
        # RSS puts the URL in <link>text</link>; Atom in <link href="...">.
        url = grab("link")
        if not url:
            m = re.search(r'<link[^>]+href="([^"]+)"', chunk, re.I)
            url = m.group(1) if m else ""
        out.append({
            "title": title[:240],
            "url": url,
            "published": (grab("pubDate") or grab("published")
                          or grab("updated") or grab("dc:date")),
            "snippet": (grab("description") or grab("summary") or grab("content"))[:300],
        })
        if len(out) >= limit:
            break
    return out


def parse_published(raw: Optional[str]) -> Optional[datetime]:
    """RFC-822 (RSS) or ISO-8601 (Atom) → aware datetime. None if unparseable."""
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
def _get(url: str, timeout: int, retries: int = 1) -> Optional[str]:
    """One GET with a single retry, decoded as UTF-8 unless told otherwise.

    Many of these feeds omit a charset; requests then assumes ISO-8859-1 and
    every rupee sign and curly quote arrives as mojibake.
    """
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            if r.status_code != 200 or not r.content:
                if attempt < retries:
                    time.sleep(0.6)
                    continue
                raise RuntimeError(f"HTTP {r.status_code}")
            enc = (r.encoding or "").lower()
            if enc in ("", "iso-8859-1", "ascii"):
                return r.content.decode("utf-8", errors="replace")
            return r.text
        except Exception:
            if attempt >= retries:
                raise
            time.sleep(0.6)
    return None


def _fetch_source(src: Source, limit: int, timeout: int) -> list[dict]:
    t0 = time.time()
    try:
        body = _get(src.url, timeout=timeout)
        items = parse_feed(body or "", limit)
        ms = int((time.time() - t0) * 1000)
        # Zero items from a 200 is a silent failure — record it as degraded so
        # it shows up as a broken feed rather than as "no news today".
        _record(src.name, bool(items), len(items), ms,
                "" if items else "returned no parseable items")
        for it in items:
            it.update(source=src.name, region=src.region,
                      category=src.category, weight=src.weight)
        return items
    except Exception as e:
        _record(src.name, False, 0, int((time.time() - t0) * 1000),
                f"{type(e).__name__}: {e}"[:120])
        return []


def _fetch_gnews(label: str, query: str, limit: int) -> list[dict]:
    t0 = time.time()
    name = f"Google News · {label}"
    try:
        from src.tools.google_news import google_news_rss
        raw = google_news_rss(query, limit=limit) or []
        items = [{
            "title": (r.get("title") or "")[:240],
            "url": r.get("url", ""),
            "published": r.get("published", ""),
            "snippet": (r.get("snippet") or "")[:300],
            "source": r.get("source") or name,
            "via": name,
            "region": "India",
            "category": "aggregator",
            "weight": 0.9,
        } for r in raw if r.get("title")]
        _record(name, bool(items), len(items), int((time.time() - t0) * 1000),
                "" if items else "no results")
        return items
    except Exception as e:
        _record(name, False, 0, int((time.time() - t0) * 1000),
                f"{type(e).__name__}: {e}"[:120])
        return []


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (t or "").lower())[:90].strip()


def fetch_all(
    limit_per_source: int = 6,
    days: Optional[int] = 7,
    timeout: int = 12,
    max_workers: int = 8,
    include_gnews: bool = True,
) -> dict:
    """Every source, in parallel, deduped and date-filtered.

    Returns ``{items, health, counts}``. Never raises.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)) if days else None
    jobs: list = []
    results: list[dict] = []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for src in FEEDS:
            jobs.append(ex.submit(_fetch_source, src, limit_per_source, timeout))
        if include_gnews:
            for label, q in GNEWS_SITE_QUERIES:
                jobs.append(ex.submit(_fetch_gnews, label, q, limit_per_source))
            for q in GNEWS_TOPICS:
                jobs.append(ex.submit(_fetch_gnews, q, q, 5))
        for fut in as_completed(jobs):
            try:
                results.extend(fut.result() or [])
            except Exception as e:            # belt and braces
                log.debug(f"news job failed: {e}")

    seen: set[str] = set()
    items: list[dict] = []
    stale = 0
    for it in results:
        key = _norm_title(it.get("title", ""))
        if not key or key in seen:
            continue
        dt = parse_published(it.get("published"))
        if cutoff and dt and dt < cutoff:
            stale += 1
            continue
        seen.add(key)
        it["published_dt"] = dt.isoformat() if dt else None
        items.append(it)

    items.sort(key=lambda i: i.get("published_dt") or "", reverse=True)
    hr = health_report()
    return {
        "items": items,
        "health": hr,
        "counts": {
            "items": len(items),
            "sources_healthy": hr["healthy"],
            "sources_total": hr["total"],
            "dropped_stale": stale,
            "dropped_duplicate": len(results) - len(items) - stale,
        },
        "as_of": datetime.now().isoformat(timespec="minutes"),
    }

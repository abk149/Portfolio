"""Market-moving events calendar + news bulletin.

Answers "what is coming that could move my book, and what just happened?"

Two very different kinds of thing live in here, and they are labelled as such
so you never mistake a guess for a fact:

  certainty = "confirmed"  the date was scraped from the body that sets it
                           (the Fed's own FOMC calendar, an RBI press release).
  certainty = "estimated"  the date was derived from the usual release pattern
                           (US payrolls on the first Friday, India CPI on the
                           12th, ...). Directionally right, worth confirming
                           before you trade on the exact day.

Nothing here is hard-coded from memory — every "confirmed" row comes off the
wire at call time, and every "estimated" row is generated from a stated rule.
"""
from __future__ import annotations

import calendar as _cal
import re
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

from src.utils.logger import get_logger

log = get_logger("market_calendar")

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
RBI_RSS = "https://www.rbi.org.in/pressreleases_rss.xml"

# RBI's feed is mostly routine plumbing — T-bill auctions, VRRR windows, SGB
# redemption prices, penalties on individual co-operative banks. None of it
# moves the market, and left unfiltered it drowns out the news that does.
_ROUTINE_RBI = re.compile(
    r"auction|treasury bill|t-bill|monetary penalty|sovereign gold bond|"
    r"redemption price|money market operation|liquidity adjustment facility|"
    r"laf\b|vrrr|vrr\b|cancellation of licence|directions issued to|"
    r"scheduled bank|banking ombudsman|premature redemption|"
    r"result of|underwriting|conversion of government")

_MONTHS = {m.lower(): i for i, m in enumerate(_cal.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(_cal.month_abbr) if m})

# Categories drive the colour/grouping in the UI.
MONETARY, INFLATION, GROWTH, JOBS = "MONETARY", "INFLATION", "GROWTH", "JOBS"
POLICY, EXPIRY, EARNINGS, TRADE = "POLICY", "EXPIRY", "EARNINGS", "TRADE"


def _ev(d: date, title: str, region: str, category: str, importance: str,
        why: str, certainty: str, source: str, url: str = "") -> dict:
    return {
        "date": d.isoformat(),
        "weekday": d.strftime("%a"),
        "title": title,
        "region": region,
        "category": category,
        "importance": importance,          # HIGH | MEDIUM | LOW
        "why": why,
        "certainty": certainty,            # confirmed | estimated
        "source": source,
        "url": url,
    }


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th `weekday` (Mon=0) of a month; n=-1 means the last one."""
    days = [d for d in range(1, _cal.monthrange(year, month)[1] + 1)
            if date(year, month, d).weekday() == weekday]
    return date(year, month, days[n if n < 0 else n - 1])


def _last_business_day(year: int, month: int) -> date:
    d = date(year, month, _cal.monthrange(year, month)[1])
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _shift_to_weekday(d: date) -> date:
    """Nudge a rule-derived date off the weekend (releases don't fall on one)."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _months_between(start: date, end: date) -> Iterable[tuple[int, int]]:
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


# ---------------------------------------------------------------------------
# Source 1 — the Fed's own FOMC calendar (confirmed)
# ---------------------------------------------------------------------------

def fetch_fomc(start: date, end: date) -> list[dict]:
    """Scrape federalreserve.gov's published FOMC schedule.

    The page is one panel per year; inside, each meeting is a
    `fomc-meeting__month` div (e.g. "January" or "Jan/Feb") followed by a
    `fomc-meeting__date` div (e.g. "27-28", "30-1", "17-18*"). The rate
    decision lands on the LAST day of the range.
    """
    import requests
    try:
        r = requests.get(FOMC_URL, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0 (PortfolioQuant)"})
        if r.status_code != 200 or not r.text:
            return []
        html = r.text
    except Exception as e:
        log.debug(f"FOMC fetch failed: {e}")
        return []

    out: list[dict] = []
    # Split into per-year blocks using the "<YYYY> FOMC Meetings" headings.
    blocks = re.split(r">(\d{4}) FOMC Meetings", html)
    # blocks = [preamble, year1, body1, year2, body2, ...]
    for i in range(1, len(blocks) - 1, 2):
        try:
            year = int(blocks[i])
        except ValueError:
            continue
        body = blocks[i + 1]
        pairs = re.findall(
            r'fomc-meeting__month[^>]*>(?:<strong>)?\s*([A-Za-z/]+).*?'
            r'fomc-meeting__date[^>]*>\s*([^<]+)',
            body, re.S)
        for month_txt, date_txt in pairs:
            if "notation" in date_txt.lower():
                continue                      # not a scheduled meeting
            nums = re.findall(r"\d+", date_txt)
            if not nums:
                continue
            last_day = int(nums[-1])
            first_day = int(nums[0])
            parts = [p for p in month_txt.split("/") if p]
            # "Jan/Feb" + "30-1" -> the decision is in February.
            name = parts[-1] if (len(parts) > 1 and last_day < first_day) else parts[0]
            mon = _MONTHS.get(name.strip().lower())
            if not mon:
                continue
            yr = year
            if mon == 1 and len(parts) > 1 and parts[0].lower().startswith("dec"):
                yr = year + 1
            try:
                d = date(yr, mon, last_day)
            except ValueError:
                continue
            if not (start <= d <= end):
                continue
            sep = "*" in date_txt   # meeting with Summary of Economic Projections
            out.append(_ev(
                d,
                "US Fed — FOMC rate decision" + (" + projections (dot plot)" if sep else ""),
                "US", MONETARY, "HIGH",
                "Sets the global cost of money. A hawkish surprise strengthens the "
                "dollar, pressures the rupee and typically triggers FII selling in "
                "Indian equities; IT and rate-sensitive names react hardest.",
                "confirmed", "federalreserve.gov", FOMC_URL))
            # Minutes are published three weeks after each meeting.
            md = d + timedelta(days=21)
            if start <= md <= end:
                out.append(_ev(
                    md, "US Fed — FOMC minutes", "US", MONETARY, "MEDIUM",
                    "Detail behind the decision; can re-price rate-cut odds.",
                    "estimated", "3 weeks after the meeting (Fed convention)", FOMC_URL))
    return out


# ---------------------------------------------------------------------------
# Source 2 — RBI press releases (confirmed where announced)
# ---------------------------------------------------------------------------

def fetch_rbi(start: date, end: date) -> tuple[list[dict], list[dict]]:
    """(events, bulletin) from RBI's own press-release feed.

    RBI announces the MPC schedule as a press release; when we find one we take
    the dates from it. Everything else in the feed becomes bulletin material —
    it's the primary source for Indian monetary/liquidity news.
    """
    from src.tools.macro_intel import _fetch_rss
    items = _fetch_rss(RBI_RSS, limit=25)
    events: list[dict] = []
    bulletin: list[dict] = []
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        low = title.lower()
        if not _ROUTINE_RBI.search(low):
            bulletin.append({"title": title, "source": "RBI (press release)",
                             "published": it.get("published"), "region": "India",
                             "snippet": (it.get("snippet") or "")[:220]})
        if "monetary policy committee" in low and "meeting" in low:
            # e.g. "... Meeting of the Monetary Policy Committee, October 6 to 8, 2026"
            for m in re.finditer(
                    r"([A-Z][a-z]+)\s+(\d{1,2})\s*(?:to|-|–)\s*(?:([A-Z][a-z]+)\s+)?(\d{1,2}),?\s*(\d{4})",
                    title + " " + (it.get("snippet") or "")):
                m1, d1, m2, d2, yr = m.groups()
                mon = _MONTHS.get((m2 or m1).lower())
                if not mon:
                    continue
                try:
                    d = date(int(yr), mon, int(d2))
                except ValueError:
                    continue
                if start <= d <= end:
                    events.append(_ev(
                        d, "RBI — MPC policy decision", "India", MONETARY, "HIGH",
                        "Repo rate, stance and liquidity. Drives banks, NBFCs, autos "
                        "and real estate directly, and the whole market's discount rate.",
                        "confirmed", "RBI press release", RBI_RSS))
                break
    return events, bulletin


# ---------------------------------------------------------------------------
# Source 3 — recurring releases, generated from their published pattern
# ---------------------------------------------------------------------------

def recurring(start: date, end: date) -> list[dict]:
    """Regular macro releases, derived from the schedule each agency keeps to.

    Every row here is `estimated`: the rule is right, the exact day can slip.
    """
    out: list[dict] = []
    for y, m in _months_between(start, end):
        add = out.append

        # --- India ---
        add(_ev(_shift_to_weekday(date(y, m, 12)),
                "India CPI inflation", "India", INFLATION, "HIGH",
                "The number the RBI targets. A hot print pushes out rate cuts and "
                "hits rate-sensitive sectors; a soft print does the reverse.",
                "estimated", "MoSPI releases CPI on the 12th"))
        add(_ev(_shift_to_weekday(date(y, m, 12)),
                "India IIP (industrial output)", "India", GROWTH, "MEDIUM",
                "Industrial demand read-through for capital goods, metals and autos.",
                "estimated", "MoSPI, released alongside CPI"))
        add(_ev(_shift_to_weekday(date(y, m, 14)),
                "India WPI inflation", "India", INFLATION, "MEDIUM",
                "Producer-side prices — a margin signal for manufacturers.",
                "estimated", "Ministry of Commerce, mid-month"))
        add(_ev(_shift_to_weekday(date(y, m, 1)),
                "India Manufacturing PMI", "India", GROWTH, "MEDIUM",
                "First hard read on the month just ended; a growth-momentum check.",
                "estimated", "S&P Global, 1st working day"))
        add(_ev(_nth_weekday(y, m, 3, -1),
                "NSE monthly F&O expiry", "India", EXPIRY, "MEDIUM",
                "Rollover and unwinding make the session volatile and volumes spike. "
                "NSE has been revising expiry weekdays — confirm the day.",
                "estimated", "last Thursday convention (verify with NSE)"))

        # --- US (sets global risk appetite and FII flows) ---
        add(_ev(_nth_weekday(y, m, 4, 1),
                "US Nonfarm Payrolls + unemployment", "US", JOBS, "HIGH",
                "The Fed's other mandate. A hot jobs print lifts US yields and the "
                "dollar, which pulls FII money out of Indian equities.",
                "estimated", "BLS, first Friday"))
        add(_ev(_shift_to_weekday(date(y, m, 12)),
                "US CPI inflation", "US", INFLATION, "HIGH",
                "The single biggest driver of global rate expectations, and so of "
                "the dollar, the rupee and FII flows into India.",
                "estimated", "BLS, around the 12th"))
        add(_ev(_last_business_day(y, m),
                "US PCE price index (Fed's preferred gauge)", "US", INFLATION, "MEDIUM",
                "The inflation measure the Fed actually targets.",
                "estimated", "BEA, last business day"))

        # --- Quarterly / annual ---
        if m in (2, 5, 8, 11):
            add(_ev(_last_business_day(y, m),
                    "India GDP (quarterly)", "India", GROWTH, "HIGH",
                    "Headline growth. Sets the market's earnings-growth assumption "
                    "and the RBI's room to move.",
                    "estimated", "MoSPI, end of Feb/May/Aug/Nov"))
        if m in (1, 4, 7, 10):
            add(_ev(_shift_to_weekday(date(y, m, 10)),
                    "India Q results season begins", "India", EARNINGS, "HIGH",
                    "IT majors report first and set the tone; banks follow. Stock-"
                    "specific risk in your holdings peaks over the next six weeks.",
                    "estimated", "results calendar, from ~10th"))
        if m == 2:
            add(_ev(date(y, 2, 1),
                    "Union Budget", "India", POLICY, "HIGH",
                    "Capex allocations, tax changes and the fiscal path — the single "
                    "largest domestic policy event for sector rotation.",
                    "estimated", "Feb 1 by convention"))

    return [e for e in out if start <= date.fromisoformat(e["date"]) <= end]


# ---------------------------------------------------------------------------
# News bulletin — what just happened
# ---------------------------------------------------------------------------

# General news feeds carry plenty that has nothing to do with markets — sport,
# obituaries, crime, entertainment. A headline has to look market-relevant AND
# not look like noise to make the bulletin.
_RELEVANT = re.compile(
    r"market|stock|share|equit|nifty|sensex|index|index|rupee|inr|dollar|yield|bond|"
    r"rate|inflation|cpi|wpi|gdp|growth|fed|rbi|ecb|policy|repo|tariff|trade|export|"
    r"import|crude|oil|gold|metal|commodit|earnings|result|profit|revenue|margin|"
    r"guidance|order book|ipo|stake|acquisit|merger|deal|fii|dii|fund|investor|"
    r"bank|nbfc|credit|liquidity|budget|fiscal|deficit|tax|gst|sector|demand|"
    r"output|pmi|production|capex|subsid|sanction|opec|recession|stimulus", re.I)
_NOISE = re.compile(
    r"\bcricket\b|\bipl\b|\bfootball\b|\btrophy\b|live streaming|match|"
    r"\bdies\b|death|obituar|passes away|horoscope|astrolog|box office|"
    r"movie|film|celebrit|actor|actress|weather|monsoon rain|festival|"
    r"recipe|health tips|viral video|arrested|murder|rape|accident", re.I)


def _is_market_news(title: str) -> bool:
    t = title or ""
    return bool(_RELEVANT.search(t)) and not _NOISE.search(t)


def bulletin(days_back: int = 5, limit: int = 40) -> list[dict]:
    """Recent, date-filtered market-moving headlines from the macro feeds."""
    from src.tools.macro_intel import _RSS_FEEDS, _fetch_rss, _parse_pub
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    seen: set[str] = set()
    out: list[dict] = []

    def add(title, source, published, snippet, region="India"):
        if not title:
            return
        key = title.strip().lower()[:80]
        if key in seen:
            return
        dt = _parse_pub(published)
        if dt and dt < cutoff:
            return                                   # stale → drop
        if not _is_market_news(title):
            return                                   # not market news → drop
        seen.add(key)
        out.append({"title": title[:200], "source": source, "region": region,
                    "published": published, "snippet": (snippet or "")[:220]})

    for name, url in _RSS_FEEDS:
        if not url:
            continue
        for it in _fetch_rss(url, limit=5):
            add(it.get("title"), name, it.get("published"), it.get("snippet"))

    # Global macro angle via Google News, so the bulletin isn't India-only.
    try:
        from src.tools.google_news import google_news_rss
        for q, region in [("Federal Reserve rate decision", "US"),
                          ("US inflation CPI report", "US"),
                          ("crude oil price OPEC", "Global"),
                          ("FII DII flows Indian equities", "India"),
                          ("RBI monetary policy repo rate", "India")]:
            for it in (google_news_rss(q, limit=4) or []):
                add(it.get("title"), it.get("source") or "Google News",
                    it.get("published"), it.get("snippet"), region)
    except Exception as e:
        log.debug(f"bulletin google news failed: {e}")

    def _key(item):
        dt = _parse_pub(item.get("published"))
        return dt or datetime.min.replace(tzinfo=timezone.utc)

    out.sort(key=_key, reverse=True)
    return _interleave(out, limit)


def _interleave(items: list[dict], limit: int, per_round: int = 1) -> list[dict]:
    """Round-robin across sources so one prolific feed can't flood the bulletin,
    preserving recency order within each source."""
    buckets: dict[str, list[dict]] = {}
    for it in items:
        buckets.setdefault(it.get("source") or "?", []).append(it)
    out: list[dict] = []
    while len(out) < limit and any(buckets.values()):
        for src in list(buckets):
            for _ in range(per_round):
                if buckets[src]:
                    out.append(buckets[src].pop(0))
                if len(out) >= limit:
                    return out
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_calendar(days_ahead: int = 60, days_back: int = 7,
                   with_bulletin: bool = True) -> dict:
    """Everything the Calendar tab needs, in one call. Sources are best-effort."""
    today = date.today()
    start = today - timedelta(days=days_back)
    end = today + timedelta(days=days_ahead)

    events: list[dict] = []
    news: list[dict] = []
    sources: list[str] = []

    try:
        fed = fetch_fomc(start, end)
        events += fed
        if fed:
            sources.append("federalreserve.gov (FOMC schedule)")
    except Exception as e:
        log.warning(f"FOMC source failed: {e}")

    try:
        rbi_events, rbi_news = fetch_rbi(start, end)
        events += rbi_events
        news += rbi_news
        sources.append("RBI press releases")
    except Exception as e:
        log.warning(f"RBI source failed: {e}")

    # If RBI hasn't published the next MPC dates in the feed window, fall back
    # to the bi-monthly cycle so the calendar isn't silently missing the single
    # most important domestic event.
    if not any(e["category"] == MONETARY and e["region"] == "India" for e in events):
        for y, m in _months_between(start, end):
            if m in (2, 4, 6, 8, 10, 12):
                d = _shift_to_weekday(date(y, m, 6))
                if start <= d <= end:
                    events.append(_ev(
                        d, "RBI — MPC policy decision (expected)", "India",
                        MONETARY, "HIGH",
                        "Repo rate, stance and liquidity. Drives banks, NBFCs, autos "
                        "and real estate, and the market's whole discount rate.",
                        "estimated", "bi-monthly MPC cycle (Feb/Apr/Jun/Aug/Oct/Dec)"))

    try:
        events += recurring(start, end)
        sources.append("published release schedules (MoSPI, BLS, BEA, NSE)")
    except Exception as e:
        log.warning(f"recurring source failed: {e}")

    if with_bulletin:
        try:
            news += bulletin(days_back=max(days_back, 5))
            sources.append("13 financial RSS feeds + Google News")
        except Exception as e:
            log.warning(f"bulletin failed: {e}")
    news = _interleave(news, 40)

    # De-duplicate (same day + same title) and sort by date, then importance.
    rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    dedup: dict[tuple, dict] = {}
    for e in events:
        key = (e["date"], e["title"])
        # A confirmed row always beats an estimated one for the same event.
        if key not in dedup or (e["certainty"] == "confirmed"
                                and dedup[key]["certainty"] != "confirmed"):
            dedup[key] = e
    events = sorted(dedup.values(),
                    key=lambda e: (e["date"], rank.get(e["importance"], 3)))

    upcoming = [e for e in events if e["date"] >= today.isoformat()]
    return {
        "as_of": datetime.now().isoformat(timespec="minutes"),
        "today": today.isoformat(),
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "events": events,
        "next_high_impact": [e for e in upcoming if e["importance"] == "HIGH"][:5],
        "bulletin": news[:40],
        "sources": sources,
        "counts": {
            "events": len(events),
            "upcoming": len(upcoming),
            "confirmed": sum(1 for e in events if e["certainty"] == "confirmed"),
            "bulletin": len(news[:40]),
        },
        "legend": ("'confirmed' dates come from the issuing body (the Fed's own "
                   "calendar, an RBI press release). 'estimated' dates are derived "
                   "from each agency's usual release pattern — right to within a "
                   "day or two, worth confirming before trading the print."),
    }

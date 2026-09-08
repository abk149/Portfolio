"""Macro-infused theme engine.

Ingests RECENT (date-filtered) macro/market signals from across the web —
domestic + global news (Google News RSS), retail chatter (Reddit), and the live
macro snapshot (VIX/PCR/USDINR) — then asks the LLM to distil them into a small
set of investable THEMES, each mapped to sectors and to specific stocks drawn
ONLY from the user's Universe Map. The result powers the "Ideas" tab and the
toward-the-frontier allocation.

Freshness is enforced: any news/post older than `days` is dropped so stale
information can't bias the call.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

from src.utils.logger import get_logger

log = get_logger("macro_intel")

# Curated macro queries that move Indian equities (domestic + global).
_QUERIES = [
    "India RBI repo rate decision",
    "India CPI inflation latest",
    "Nifty sector outlook this week",
    "India free trade agreement deal",
    "FII DII flows India equities",
    "crude oil price impact India",
    "US Fed interest rate India markets",
    "India GDP growth forecast",
    "rupee USDINR outlook",
    "India government policy PLI sector",
    "global markets impact on India today",
    "India earnings results sector",
]
_SUBREDDITS = [
    ("IndianStreetBets", "macro OR sector OR rate OR result"),
    ("IndiaInvestments", "market OR sector OR rate OR policy"),
    ("StockMarketIndia", "nifty OR sector OR macro"),
    ("DalalStreetTalks", "sector OR result OR macro"),
    ("IndianStockMarket", "nifty OR sector OR rate"),
    ("NSEbets", "sector OR result"),
]

# 12-15 distinct financial-news RSS feeds beyond Reddit. Best-effort per feed.
_RSS_FEEDS = [
    ("Economic Times Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("ET Economy", "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms"),
    ("Moneycontrol Markets", "https://www.moneycontrol.com/rss/marketreports.xml"),
    ("Moneycontrol Business", "https://www.moneycontrol.com/rss/business.xml"),
    ("LiveMint Markets", "https://www.livemint.com/rss/markets"),
    ("LiveMint Economy", "https://www.livemint.com/rss/economy"),
    ("Business Standard Markets", "https://www.business-standard.com/rss/markets-106.rss"),
    ("BusinessLine Markets", "https://www.thehindubusinessline.com/markets/feeder/default.rss"),
    ("Financial Express Market", "https://www.financialexpress.com/market/feed/"),
    ("Financial Express Economy", "https://www.financialexpress.com/economy/feed/"),
    ("NDTV Profit", "https://feeds.feedburner.com/ndtvprofit-latest"),
    ("Investing.com India", "https://in.investing.com/rss/news_25.rss"),
    ("Zeebiz Markets", "https://www.zeebiz.com/rss/markets.xml"),
    ("Reuters India (GN)", None),   # via Google News fallback
]


def _fetch_rss(url: str, limit: int = 8) -> list[dict]:
    """Deprecated shim — `news_sources` owns feed fetching now. Kept because
    market_calendar imports it for the RBI feed."""
    """Generic RSS/Atom reader → [{title, url, published}]. Best-effort."""
    import re
    import requests
    try:
        r = requests.get(url, timeout=12,
                         headers={"User-Agent": "Mozilla/5.0 (PortfolioQuant)"})
        if r.status_code != 200 or not r.content:
            return []
        # Many of these feeds omit a charset, and requests then defaults to
        # ISO-8859-1 for text/*, turning every curly quote and rupee sign into
        # mojibake ("India â Bulletin"). Decode as UTF-8 unless told otherwise.
        if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
            text = r.content.decode("utf-8", errors="replace")
        else:
            text = r.text
        items = re.split(r"<item[ >]|<entry[ >]", text)[1:]
        out = []
        for it in items[:limit * 2]:
            def grab(tag):
                m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", it, re.S | re.I)
                if not m:
                    return ""
                v = re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))
                return re.sub(r"<[^>]+>", " ", v).strip()
            title = grab("title")
            pub = grab("pubDate") or grab("published") or grab("updated")
            if title:
                out.append({"title": title[:200], "source": "RSS", "published": pub,
                            "snippet": grab("description")[:200]})
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        log.debug(f"rss {url} failed: {e}")
        return []


def _parse_pub(pub: Optional[str]) -> Optional[datetime]:
    if not pub:
        return None
    try:
        dt = parsedate_to_datetime(pub)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def gather_signals(days: int = 14, per_query: int = 5) -> dict:
    """Collect recent, dated macro signals. Every source is best-effort.

    News comes from the shared backbone in `news_sources` (dozens of feeds,
    fetched in parallel with per-source health tracking) rather than a private
    list here — the old list had three sources that had been silently returning
    nothing for a while.

    Reddit is included ONLY where a post is independently corroborated by that
    news pool. Anonymous chatter that nothing else confirms is discarded before
    the model ever sees it, and the count of what was dropped is reported.
    """
    macro = {}
    try:
        from src.tools.macro import MacroSnapshot
        macro = MacroSnapshot().market_mode()
    except Exception as e:
        log.debug(f"macro snapshot failed: {e}")

    news: list[dict] = []
    health: dict = {}
    try:
        from src.tools.news_sources import fetch_all
        pool = fetch_all(limit_per_source=per_query + 1, days=days)
        news = pool["items"]
        health = pool["health"]
    except Exception as e:
        log.warning(f"news backbone failed: {e}")

    reddit: list[dict] = []
    reddit_report: dict = {}
    try:
        from src.tools.reddit import fetch_verified
        vr = fetch_verified(news_items=news, limit=25, total_budget_s=20.0)
        reddit = vr["verified"]
        reddit_report = {"counts": vr["counts"], "note": vr["note"],
                         "health": vr.get("reddit_health", {})}
    except Exception as e:
        log.debug(f"reddit gather failed: {e}")
        reddit_report = {"note": f"Reddit unavailable ({e}). Skipped — it is an "
                                 f"optional, cross-checked sentiment source."}

    return {
        "as_of": datetime.now().isoformat(timespec="minutes"),
        "window_days": days,
        "macro": macro,
        "news": news[:90],
        "reddit": reddit[:20],
        "reddit_report": reddit_report,
        "source_health": health,
        "sources_used": health.get("healthy", 0),
    }


def _universe_for_llm(limit: int = 120) -> list[dict]:
    """Compact universe slice for grounding: prefer scored / BUY names, keep
    sector coverage. Only these symbols may be recommended."""
    try:
        from src.kb import KnowledgeBase
        rows = KnowledgeBase.get().all_stocks() or []
    except Exception as e:
        log.debug(f"universe pull failed: {e}")
        return []
    def _key(s):
        reco = (s.get("recommendation") or "")
        rank = 0 if reco in ("STRONG_BUY", "BUY") else 1
        return (rank, -(s.get("combined") or s.get("tech_score") or 0))
    rows = sorted(rows, key=_key)[:limit]
    return [{
        "symbol": s.get("symbol"), "sector": s.get("sector"),
        "recommendation": s.get("recommendation"),
        "score": s.get("combined") or s.get("tech_score"),
    } for s in rows if s.get("symbol")]


def _latest_quant() -> list[str]:
    """Symbols validated by the most recent DR-Quant run, if any."""
    try:
        import glob
        from config import settings
        files = sorted(glob.glob(str(settings.cache_dir / "quant_runs" / "*.json")),
                       key=lambda p: p)
        if not files:
            return []
        with open(files[-1]) as f:
            res = json.load(f)
        return [str(v.get("symbol") or v.get("ticker")) for v in (res.get("validated") or [])][:15]
    except Exception:
        return []


def build_macro_themes(days: int = 14, max_themes: int = 6) -> dict:
    """Ingest → synthesize themes → map to universe stocks. Returns:
    {as_of, window_days, macro, themes:[{theme, driver, sectors, sentiment,
     horizon, stocks:[{symbol, rationale}]}], tickers:[...]}"""
    signals = gather_signals(days)
    universe = _universe_for_llm()
    quant = _latest_quant()

    if not universe:
        return {"error": "Universe is empty — build the Universe Map first so I "
                         "have stocks to map themes onto.", **signals}

    uni_str = "\n".join(
        f"- {u['symbol']} | {u.get('sector') or '?'} | {u.get('recommendation') or '-'} | score {u.get('score')}"
        for u in universe)
    news_str = "\n".join(f"- [{n.get('published') or '?'}] {n['title']} ({n.get('source')})"
                         for n in signals["news"][:50])
    reddit_str = "\n".join(
        f"- r/{r.get('subreddit')}: {r.get('title')}"
        + (f"  [corroborated by {', '.join(sorted({e['source'] for e in r['corroboration']['sources']}))}]"
           if r.get("corroboration") else "")
        for r in signals["reddit"][:20])
    macro = signals.get("macro") or {}
    macro_str = (f"VIX {macro.get('india_vix')} · PCR {macro.get('nifty_pcr')} · "
                 f"USDINR {macro.get('usdinr')} · mode {macro.get('mode')}\n"
                 + "\n".join(f"  • {r}" for r in (macro.get('reasons') or [])))

    system = (
        "You are the CIO of an Indian-equities fund. You weigh ALL macro forces "
        "TOGETHER — interest rates, inflation, currency (USDINR), crude/commodities, "
        "FII/DII flows, global risk (Fed, geopolitics), sector momentum and "
        "sentiment — into ONE integrated, holistic view. You do NOT pick a stock "
        "for a single factor; each pick must survive the combined picture. Use "
        "ONLY information within the stated window. Output STRICT JSON only.")
    prompt = f"""As of {signals['as_of']} (use ONLY signals from the last {days} days).

MACRO SNAPSHOT:
{macro_str}

RECENT NEWS (domestic + global affecting India, {len(signals['news'])} items from {signals.get('sources_used')} sources):
{news_str or '(none)'}

RETAIL CHATTER (Reddit — ONLY posts independently corroborated by a named news
source are listed; uncorroborated chatter has been discarded):
{reddit_str or '(none — nothing from social passed cross-verification)'}

DR-QUANT validated this run: {', '.join(quant) or '(none)'}

INVESTABLE UNIVERSE (you may ONLY recommend symbols from this list):
{uni_str}

TASK:
1. Synthesise a single HOLISTIC macro view that integrates EVERY factor above
   (rates + inflation + INR + crude + flows + global + sentiment) — how they
   net out for Indian equities right now.
2. Select 3 to 7 stocks FROM THE UNIVERSE that are best positioned under that
   COMBINED view (not single-factor bets). For each, give a DETAILED thesis
   (3-5 sentences) that explicitly weighs MULTIPLE factors for/against it, plus
   the key risk. Prefer names the combined picture supports with conviction.

Return STRICT JSON, no prose:
{{"macro_view":"integrated paragraph weighing all factors",
"picks":[{{"symbol":"<from universe>","sector":"...","conviction":"HIGH|MEDIUM|LOW",
"thesis":"detailed multi-factor analysis (3-5 sentences) + key risk"}}]}}"""

    from src.llm import get_llm
    reply = get_llm().complete(system, prompt) or ""
    try:
        from src.llm.ollama_provider import _extract_json
        data = _extract_json(reply)
    except Exception:
        data = None
    if not isinstance(data, dict) or "picks" not in data:
        return {"error": "LLM did not return usable picks.",
                "raw": reply[:600], **signals}

    # Validate against the universe; attach sector + a quant entry price per pick.
    by_sym = {u["symbol"]: u for u in universe}
    try:
        from src.tools.deep_dive import _entry_price
    except Exception:
        _entry_price = lambda s: {}
    tickers: list[str] = []
    picks = []
    for p in (data.get("picks") or [])[:7]:
        sym = str(p.get("symbol") or "").upper().strip()
        if sym not in by_sym or sym in tickers:
            continue
        u = by_sym[sym]
        picks.append({
            "symbol": sym,
            "sector": p.get("sector") or u.get("sector"),
            "conviction": str(p.get("conviction", "MEDIUM")).upper(),
            "thesis": p.get("thesis", ""),
            "recommendation": u.get("recommendation"),
            "entry": _entry_price(sym),
        })
        tickers.append(sym)

    return {
        "as_of": signals["as_of"], "window_days": days,
        "macro": macro, "macro_view": data.get("macro_view", ""),
        "picks": picks, "tickers": tickers,
        "counts": {"news": len(signals["news"]), "reddit": len(signals["reddit"]),
                   "universe": len(universe), "sources": signals.get("sources_used")},
    }

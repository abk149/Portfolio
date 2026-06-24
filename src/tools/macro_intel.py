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
    """Generic RSS/Atom reader → [{title, url, published}]. Best-effort."""
    import re
    import requests
    try:
        r = requests.get(url, timeout=12,
                         headers={"User-Agent": "Mozilla/5.0 (PortfolioQuant)"})
        if r.status_code != 200 or not r.text:
            return []
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
    """Collect recent, dated macro signals. Every source is best-effort."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    macro = {}
    try:
        from src.tools.macro import MacroSnapshot
        macro = MacroSnapshot().market_mode()
    except Exception as e:
        log.debug(f"macro snapshot failed: {e}")

    news: list[dict] = []
    seen_titles: set = set()

    def _add_news(topic, title, source, published, snippet):
        if not title:
            return
        key = title.strip().lower()[:80]
        if key in seen_titles:
            return
        dt = _parse_pub(published)
        if dt and dt < cutoff:
            return                                # too old → drop (no stale bias)
        seen_titles.add(key)
        news.append({"topic": topic, "title": title, "source": source,
                     "published": published, "snippet": (snippet or "")[:240]})

    # Source 1: Google News RSS over curated macro queries.
    try:
        from src.tools.google_news import google_news_rss
        for q in _QUERIES:
            for it in (google_news_rss(q, limit=per_query) or []):
                _add_news(q, it.get("title"), it.get("source"),
                          it.get("published"), it.get("snippet"))
    except Exception as e:
        log.debug(f"google news failed: {e}")

    # Sources 2-14: distinct financial-news RSS feeds.
    for name, url in _RSS_FEEDS:
        if not url:
            continue
        for it in _fetch_rss(url, limit=6):
            _add_news(name, it.get("title"), name, it.get("published"), it.get("snippet"))

    reddit: list[dict] = []
    try:
        from src.tools.reddit import _do_search
        for sub, q in _SUBREDDITS:
            for it in (_do_search(q, sub, 6, sort="new") or []):
                cu = it.get("created_utc")
                if cu and datetime.fromtimestamp(cu, tz=timezone.utc) < cutoff:
                    continue
                reddit.append({
                    "subreddit": it.get("subreddit"), "title": it.get("title"),
                    "score": it.get("score"), "snippet": (it.get("snippet") or "")[:200],
                })
    except Exception as e:
        log.debug(f"reddit gather failed: {e}")

    return {
        "as_of": datetime.now().isoformat(timespec="minutes"),
        "window_days": days,
        "macro": macro,
        "news": news[:90],
        "reddit": reddit[:30],
        "sources_used": len([u for _, u in _RSS_FEEDS if u]) + 1 + len(_SUBREDDITS),
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
    reddit_str = "\n".join(f"- r/{r['subreddit']}: {r['title']}" for r in signals["reddit"][:20])
    macro = signals.get("macro") or {}
    macro_str = (f"VIX {macro.get('india_vix')} · PCR {macro.get('nifty_pcr')} · "
                 f"USDINR {macro.get('usdinr')} · mode {macro.get('mode')}\n"
                 + "\n".join(f"  • {r}" for r in (macro.get('reasons') or [])))

    system = (
        "You are a buy-side macro strategist for Indian equities. You connect "
        "recent macro/market developments to sectors and to specific stocks. You "
        "are rigorous, cite the driver, and NEVER use information older than the "
        "stated window. Output STRICT JSON only.")
    prompt = f"""As of {signals['as_of']} (use ONLY signals from the last {days} days).

MACRO SNAPSHOT:
{macro_str}

RECENT NEWS (domestic + global affecting India):
{news_str or '(none)'}

RETAIL CHATTER (Reddit):
{reddit_str or '(none)'}

DR-QUANT validated this run: {', '.join(quant) or '(none)'}

INVESTABLE UNIVERSE (you may ONLY recommend symbols from this list):
{uni_str}

TASK: Identify up to {max_themes} investable THEMES driven by the above. For each,
map it to the impacted sector(s) and pick 2-5 stocks FROM THE UNIVERSE that best
express it, each with a one-line rationale tying the stock to the macro driver.
Skip a theme if no universe stock fits.

Return STRICT JSON, no prose:
{{"themes":[{{"theme":"...","driver":"... (cite the recent development)","sectors":["..."],
"sentiment":"BULLISH|BEARISH|NEUTRAL","horizon":"weeks|months|quarters",
"stocks":[{{"symbol":"<from universe>","rationale":"..."}}]}}]}}"""

    from src.llm import get_llm
    reply = get_llm().complete(system, prompt) or ""
    try:
        from src.llm.ollama_provider import _extract_json
        data = _extract_json(reply)
    except Exception:
        data = None
    if not isinstance(data, dict) or "themes" not in data:
        return {"error": "LLM did not return usable themes.",
                "raw": reply[:600], **signals}

    # Keep only universe symbols; attach sector/score; collect tickers for allocation.
    by_sym = {u["symbol"]: u for u in universe}
    tickers: list[str] = []
    clean_themes = []
    for t in (data.get("themes") or [])[:max_themes]:
        stocks = []
        for s in (t.get("stocks") or []):
            sym = str(s.get("symbol") or "").upper().strip()
            if sym in by_sym:
                u = by_sym[sym]
                stocks.append({"symbol": sym, "rationale": s.get("rationale", ""),
                               "sector": u.get("sector"), "recommendation": u.get("recommendation")})
                if sym not in tickers:
                    tickers.append(sym)
        if stocks:
            clean_themes.append({
                "theme": t.get("theme", ""), "driver": t.get("driver", ""),
                "sectors": t.get("sectors", []),
                "sentiment": str(t.get("sentiment", "NEUTRAL")).upper(),
                "horizon": t.get("horizon", ""), "stocks": stocks,
            })

    return {
        "as_of": signals["as_of"], "window_days": days,
        "macro": macro, "themes": clean_themes,
        "tickers": tickers,
        "counts": {"news": len(signals["news"]), "reddit": len(signals["reddit"]),
                   "universe": len(universe)},
    }

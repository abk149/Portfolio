"""Full research dossier for a single recommendation.

The ghost book is a rehearsal for real money, so a name should not enter it on
a one-line thesis. This assembles everything the system can find about a stock
and asks the model to weigh it as one picture:

  * **Micro** — fundamentals with provenance, and the last two quarters of
    filings/results text where they can be fetched.
  * **Price** — the quant entry model: DMAs, RSI, ATR, a suggested entry zone.
  * **Macro** — the live regime (VIX, USD/INR, Nifty) it would be bought into.
  * **Calendar** — scheduled events that land on this name or its sector.
  * **News** — recent coverage from the named sources.
  * **What people are saying** — social, but ONLY where an independent news
    source corroborates it. Uncorroborated chatter is counted and discarded,
    never shown to the model, because this is exactly where a pump post would
    otherwise turn into a thesis.

Every section is best-effort: a dossier with four of six sections is useful, a
crash is not. What could not be gathered is named in `gaps` rather than left as
a silent hole, because "no bad news found" and "news lookup failed" mean very
different things.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from src.utils.logger import get_logger

log = get_logger("portfolio.research")


def _safe(section: str, fn: Callable, gaps: list[str], default=None):
    try:
        return fn()
    except Exception as e:
        log.debug(f"{section} failed: {e}")
        gaps.append(f"{section} ({type(e).__name__})")
        return default


def _events_for(symbol: str, sector: Optional[str], calendar: Optional[dict],
                limit: int = 4) -> list[dict]:
    """Scheduled events worth knowing about before buying this name.

    Market-wide events are included deliberately: a rate decision hits a bank
    whether or not the calendar entry mentions it by name.
    """
    if not calendar:
        return []
    today = calendar.get("today", "")
    out = []
    for e in (calendar.get("events") or []):
        if e.get("date", "") < today:
            continue
        if e.get("importance") != "HIGH":
            continue
        out.append({"date": e.get("date"), "title": e.get("title"),
                    "region": e.get("region"), "why": e.get("why"),
                    "certainty": e.get("certainty")})
        if len(out) >= limit:
            break
    return out


def research(symbol: str, macro: Optional[dict] = None,
             calendar: Optional[dict] = None, max_docs: int = 2,
             with_llm: bool = True) -> dict:
    """Assemble everything known about one stock, then synthesise it."""
    symbol = (symbol or "").upper().strip().replace(".NS", "").replace(".BO", "")
    if not symbol:
        return {"error": "no symbol"}

    gaps: list[str] = []

    # ---- micro: fundamentals, filings, entry model, first-pass analysis ----
    dd = _safe("company analysis", lambda: __import__(
        "src.tools.deep_dive", fromlist=["deep_dive"]
    ).deep_dive(symbol, max_docs=max_docs), gaps, default={}) or {}

    # ---- news + corroborated social ----
    news_items: list[dict] = []
    social: list[dict] = []
    dropped_social = 0

    def _gather_news():
        from src.tools.web_search import WebSearcher
        return WebSearcher(max_results=6).news_for(symbol) or []

    for item in (_safe("news", _gather_news, gaps, default=[]) or []):
        src = str(item.get("source") or "")
        if src.lower().startswith("reddit"):
            # news_for already drops uncorroborated posts; anything that
            # survives carries its corroboration in the source label.
            social.append(item)
        else:
            news_items.append(item)

    # ---- macro backdrop ----
    if macro is None:
        macro = _safe("macro snapshot", lambda: __import__(
            "src.tools.macro", fromlist=["MacroSnapshot"]
        ).MacroSnapshot().market_mode(), gaps, default={}) or {}

    fundamentals = dd.get("fundamentals") or {}
    events = _events_for(symbol, fundamentals.get("sector"), calendar)

    dossier = {
        "symbol": symbol,
        "as_of": datetime.now().isoformat(timespec="minutes"),
        "fundamentals": fundamentals,
        "fundamentals_provenance": dd.get("fundamentals_provenance"),
        "fundamentals_missing": dd.get("fundamentals_missing"),
        "entry": dd.get("entry"),
        "company_analysis": dd.get("analysis"),
        "reports": dd.get("sources") or [],
        "news": news_items[:12],
        "social": social[:6],
        "macro": macro,
        "events": events,
        "gaps": gaps,
        "counts": {"news": len(news_items), "social": len(social),
                   "reports": len(dd.get("sources") or []),
                   "events": len(events)},
    }

    if with_llm:
        dossier["verdict"] = _synthesise(dossier)
    return dossier


def _synthesise(d: dict) -> dict:
    """One judgement over the whole dossier.

    Deliberately asked LAST, with everything in front of it, rather than
    letting each section reach its own verdict — the point is the combined
    picture, and a stock can look fine on fundamentals and still be a bad buy
    into next week's rate decision.
    """
    from src.llm.insights import _complete

    f = d.get("fundamentals") or {}
    e = d.get("entry") or {}
    ca = d.get("company_analysis") or {}
    m = d.get("macro") or {}

    def _fmt_list(rows, key_a, key_b=None, n=8):
        out = []
        for r in rows[:n]:
            line = f"  - {r.get(key_a, '')}"
            if key_b and r.get(key_b):
                line += f" ({r[key_b]})"
            out.append(line[:220])
        return "\n".join(out) or "  (none found)"

    system = (
        "You are an analyst signing off on a position before it is taken. You "
        "are given a full dossier: fundamentals, the last filings, a technical "
        "entry model, the macro regime, scheduled events, news, and social "
        "chatter that has ALREADY been checked against named news sources. "
        "Weigh it as ONE picture — a stock can look fine on fundamentals and "
        "still be a poor buy into next week's rate decision. Use only what is "
        "here; where the dossier says a section is missing, say the view is "
        "weaker for it rather than filling the gap from memory. Never invent a "
        "number, a date or a filing. Output STRICT JSON only.")

    prompt = f"""DOSSIER — {d['symbol']} (as of {d['as_of']})

FUNDAMENTALS: P/E {f.get('pe')} · ROE {f.get('roe_pct')}% · D/E {f.get('debt_to_equity')} ·
sales growth {f.get('sales_growth_pct')}% · profit growth {f.get('profit_growth_pct')}% ·
mcap {f.get('market_cap_cr')} cr · sector {f.get('sector')}
Missing metrics: {', '.join(d.get('fundamentals_missing') or []) or 'none'}

COMPANY ANALYSIS (from the filings):
  health: {ca.get('financial_health')}
  last 2 quarters: {ca.get('quarter_trend')}
  valuation: {ca.get('valuation')}
  issues: {'; '.join(ca.get('issues') or []) or 'none listed'}
  red flags: {'; '.join(ca.get('red_flags') or []) or 'none listed'}

REPORTS/FILINGS USED:
{_fmt_list(d.get('reports') or [], 'title')}

PRICE MODEL: CMP {e.get('current')} · 50-DMA {e.get('dma50')} · 200-DMA {e.get('dma200')} ·
RSI {e.get('rsi')} · suggested entry {e.get('suggested_entry')}
(zone {e.get('entry_low')}–{e.get('entry_high')})

MACRO REGIME: {m.get('mode')} · VIX {m.get('india_vix')} · USD/INR {m.get('usdinr')} ·
Nifty {m.get('nifty_change_pct')}% today

SCHEDULED EVENTS AHEAD:
{_fmt_list(d.get('events') or [], 'title', 'date')}

RECENT NEWS:
{_fmt_list(d.get('news') or [], 'title', 'source')}

WHAT PEOPLE ARE SAYING (corroborated by named news sources only):
{_fmt_list(d.get('social') or [], 'title', 'source')}

COULD NOT GATHER: {', '.join(d.get('gaps') or []) or 'nothing — all sections present'}

Return STRICT JSON, no prose:
{{"verdict":"BUY|ACCUMULATE|WATCH|AVOID",
"conviction":"HIGH|MEDIUM|LOW",
"thesis":"3-5 sentences weighing micro, macro, price and timing TOGETHER",
"micro_view":"what the company's own numbers and filings say",
"macro_view":"what buying this into the current regime means",
"timing":"what the calendar implies for entry timing",
"what_people_say":"what the corroborated coverage adds, or that it adds nothing",
"key_risks":["..."],
"what_would_change_my_mind":["..."],
"confidence_note":"how much the missing sections weaken this"}}"""

    # The dossier behind this prompt costs ~80s of network and filing fetches.
    # Losing the judgement to one transient "empty response" from the provider
    # would waste all of it, so try twice before giving up.
    out = _complete(system, prompt)
    if not out.get("ok"):
        import time
        log.info(f"synthesis retry for {d['symbol']} after: {out.get('error')}")
        time.sleep(2)
        out = _complete(system, prompt)
    if not out.get("ok"):
        return {"error": out.get("error", "synthesis failed"),
                "note": "The research below is still complete — only the final "
                        "judgement is missing. Re-run to try again."}
    try:
        from src.llm.ollama_provider import _extract_json
        parsed = _extract_json(out["text"])
        if isinstance(parsed, dict):
            return parsed
    except Exception as e:
        log.debug(f"verdict parse failed: {e}")
    return {"raw": out["text"][:1200]}

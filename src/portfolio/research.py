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


def _safe(section: str, fn: Callable, gaps: list[str], status: dict, default=None):
    """Run one section, recording whether it worked.

    Per-section status exists so a partial dossier can be repaired instead of
    rebuilt: fetching filings and news takes about a minute, and losing all of
    it because the model was briefly unavailable is pure waste. It also lets the
    UI offer a retry on exactly the part that broke.
    """
    try:
        out = fn()
        status[section] = {"ok": True}
        return out
    except Exception as e:
        log.debug(f"{section} failed: {e}")
        gaps.append(f"{section} ({type(e).__name__})")
        status[section] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
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


# Which parts of a dossier can be rebuilt on their own.
SCOPES = ("all", "documents", "news", "judgement")


def research(symbol: str, macro: Optional[dict] = None,
             calendar: Optional[dict] = None, max_docs: int = 2,
             with_llm: bool = True, scope: str = "all",
             previous: Optional[dict] = None) -> dict:
    """Assemble everything known about one stock, then synthesise it.

    `scope` allows repairing a partial dossier instead of rebuilding it:

      * ``documents`` — re-fetch filings/fundamentals only (the slow part),
      * ``news``      — re-fetch coverage only,
      * ``judgement`` — re-run just the final synthesis over what's already
        stored, which is seconds rather than a minute,
      * ``all``       — everything.

    Anything not in scope is carried over from `previous`, so a retry never
    costs work that already succeeded.
    """
    symbol = (symbol or "").upper().strip().replace(".NS", "").replace(".BO", "")
    if not symbol:
        return {"error": "no symbol"}

    prev = previous or {}
    gaps: list[str] = []
    status: dict = {}

    # ---- judgement-only: reuse the stored dossier entirely ----
    if scope == "judgement":
        if not prev:
            return {"error": "Nothing stored to re-judge — run the full "
                             "analysis for this stock first."}
        dossier = dict(prev)
        dossier["verdict"] = _synthesise(dossier)
        dossier["sections"] = dict(prev.get("sections") or {})
        dossier["sections"]["judgement"] = (
            {"ok": False, "error": dossier["verdict"].get("error")}
            if dossier["verdict"].get("error") else {"ok": True})
        dossier["as_of"] = datetime.now().isoformat(timespec="minutes")
        return _finalise(dossier)

    # ---- micro: fundamentals, filings, entry model, first-pass analysis ----
    if scope in ("all", "documents") or not prev:
        dd = _safe("company analysis", lambda: __import__(
            "src.tools.deep_dive", fromlist=["deep_dive"]
        ).deep_dive(symbol, max_docs=max_docs), gaps, status, default={}) or {}
    else:
        dd = {"fundamentals": prev.get("fundamentals"),
              "fundamentals_provenance": prev.get("fundamentals_provenance"),
              "fundamentals_missing": prev.get("fundamentals_missing"),
              "entry": prev.get("entry"), "analysis": prev.get("company_analysis"),
              "sources": prev.get("reports") or []}
        status["company analysis"] = (prev.get("sections") or {}).get(
            "company analysis", {"ok": True})

    # ---- news + corroborated social ----
    if scope in ("all", "news") or not prev:
        news_items: list[dict] = []
        social: list[dict] = []

        def _gather_news():
            from src.tools.web_search import WebSearcher
            return WebSearcher(max_results=6).news_for(symbol) or []

        for item in (_safe("news", _gather_news, gaps, status, default=[]) or []):
            src = str(item.get("source") or "")
            if src.lower().startswith("reddit"):
                # news_for already drops uncorroborated posts; anything that
                # survives carries its corroboration in the source label.
                social.append(item)
            else:
                news_items.append(item)
    else:
        news_items = list(prev.get("news") or [])
        social = list(prev.get("social") or [])
        status["news"] = (prev.get("sections") or {}).get("news", {"ok": True})

    # ---- macro backdrop ----
    if macro is None:
        macro = _safe("macro snapshot", lambda: __import__(
            "src.tools.macro", fromlist=["MacroSnapshot"]
        ).MacroSnapshot().market_mode(), gaps, status, default={}) or {}
    else:
        status["macro snapshot"] = {"ok": True}

    fundamentals = dd.get("fundamentals") or {}
    events = _events_for(symbol, fundamentals.get("sector"), calendar)

    # ---- the uniform channels: identical for every candidate ----
    #
    # Whatever engine suggested this name, it now gets the same four analyses,
    # so the ghost track record measures the IDEAS rather than which door they
    # came through.
    if scope in ("all", "documents") or not prev.get("factors"):
        factors = _safe("factor exposure", lambda: __import__(
            "src.portfolio.factors", fromlist=["sensitivities"]
        ).sensitivities(symbol, years=3), gaps, status, default={}) or {}
        runway_d = _safe("runway", lambda: __import__(
            "src.portfolio.factors", fromlist=["runway"]
        ).runway(symbol), gaps, status, default={}) or {}
        season = _safe("seasonality", lambda: __import__(
            "src.portfolio.seasonality", fromlist=["seasonality"]
        ).seasonality(symbol, years=5), gaps, status, default={}) or {}
    else:
        factors = prev.get("factors") or {}
        runway_d = prev.get("runway") or {}
        season = prev.get("seasonality") or {}
        for k in ("factor exposure", "runway", "seasonality"):
            status[k] = (prev.get("sections") or {}).get(k, {"ok": True})

    gate = _safe("screening gate", lambda: __import__(
        "src.portfolio.screening", fromlist=["uniform_gate"]
    ).uniform_gate(symbol, fundamentals, runway_d, factors, macro),
        gaps, status, default={}) or {}

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
        "factors": factors,
        "runway": runway_d,
        "seasonality": season,
        "gate": gate,
        "gaps": gaps,
        "sections": status,
        "counts": {"news": len(news_items), "social": len(social),
                   "reports": len(dd.get("sources") or []),
                   "events": len(events)},
    }

    if with_llm:
        dossier["verdict"] = _synthesise(dossier)
        status["judgement"] = ({"ok": False, "error": dossier["verdict"].get("error")}
                               if dossier["verdict"].get("error") else {"ok": True})
    return _finalise(dossier)


def _finalise(d: dict) -> dict:
    """Mark whether the dossier is whole, and what still needs a retry.

    Surfaced rather than inferred in the UI: a dossier missing its filings is
    materially weaker than one missing only its final judgement, and the user
    should be able to retry exactly the part that failed.
    """
    sections = d.get("sections") or {}
    failed = [name for name, st in sections.items() if not st.get("ok")]
    d["failed_sections"] = failed
    d["complete"] = not failed
    d["retryable"] = sorted({
        "documents" if f == "company analysis" else
        "news" if f == "news" else
        "judgement" if f == "judgement" else "all"
        for f in failed
    })
    return d


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
    fa = d.get("factors") or {}
    rw = d.get("runway") or {}
    se = d.get("seasonality") or {}
    ga = d.get("gate") or {}

    def _fmt_factors(fo: dict) -> str:
        rows = (fo.get("factors") or [])
        if not rows:
            return "  (not measured)"
        return "\n".join(
            f"  - {r['label']}: {r['beta']:+.2f} beta"
            f" (joint {r.get('beta_joint')}), corr {r['correlation']:+.2f},"
            f" t={r.get('t_stat')}{'  <- material' if r.get('material') else ''}"
            for r in rows)

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
        "entry model, measured macro sensitivities, how much of the move has "
        "already happened, month-by-month seasonality, the macro regime, "
        "scheduled events, news, and social chatter that has ALREADY been "
        "checked against named news sources.\n\n"
        "Your job is FORWARD-LOOKING. Explaining why a stock rose is worthless "
        "— by the time a reason is in the news it is in the price. Reason about "
        "what could happen NEXT: which of the listed events or macro moves "
        "would re-rate this name, in which direction, and what is not yet "
        "priced in. Where the dossier says the move has largely been made, say "
        "so plainly and do not dress up a chase as a thesis.\n\n"
        "Use the measured sensitivities rather than assumptions about what a "
        "sector 'should' do — if the numbers say this stock falls when the "
        "rupee weakens, that is the fact, whatever the textbook says. Weigh it "
        "as ONE picture. Use only what is here; where a section is missing, say "
        "the view is weaker for it rather than filling the gap from memory. "
        "Never invent a number, a date or a filing. Output STRICT JSON only.")

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

MEASURED MACRO SENSITIVITY (from {fa.get('weeks', '?')} weeks of returns; these
are what the data says, not what the sector is supposed to do):
{_fmt_factors(fa)}
  R-squared {fa.get('r_squared')} — how much of its weekly moves the factors explain.

HOW MUCH OF THE MOVE IS ALREADY DONE:
  {rw.get('stance', 'unknown')} (score {rw.get('runway_score')}/100)
  3m {rw.get('ret_3m')}% · 6m {rw.get('ret_6m')}% · {rw.get('from_52w_high_pct')}% from
  the 52-week high · RSI {rw.get('rsi')} · {rw.get('above_200dma_pct')}% above its 200-DMA
  {rw.get('reading', '')}

SEASONALITY: {se.get('verdict', 'unknown')} — {se.get('reading', '')}

REQUIRED-CHECK RESULT: {ga.get('summary', 'not run')}
{chr(10).join('  - ' + c['label'] + ': ' + c['status'] + ' (' + str(c.get('detail'))[:90] + ')' for c in (ga.get('checks') or [])[:8])}

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
"thesis":"3-5 sentences on what could happen NEXT and why it isn't priced in yet",
"micro_view":"what the company's own numbers and filings say",
"macro_view":"what the MEASURED sensitivities mean for this name from here",
"move_left":"honest read on whether the move has already happened",
"catalysts":["specific things that could re-rate this, with direction"],
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

"""LLM applications that turn the app's computed data into decisions.

The app already computes a lot of good numbers — a performance report, a
benchmark comparison, an event calendar, a holdings snapshot. Numbers alone
still leave the user to work out *what to do about it*. These functions close
that gap.

Every one of them follows the same discipline:

  * **Grounded.** The prompt carries the user's real numbers. The model is told,
    explicitly, to use only what it's given and to say when something is
    missing. It is never asked to recall a market fact from training.
  * **Cheap.** Context is compacted to the few dozen lines that matter, so this
    stays usable on the slow NVIDIA models the phone build can reach.
  * **Honest about failure.** Providers signal errors with a "[provider …]"
    sentinel; we detect it and return ok=False rather than rendering it.

Applications:
  ``daily_brief``          what today's calendar + news means for THIS book
  ``performance_review``   why you're beating/lagging the index, and what to fix
  ``risk_review``          concentration, correlation and event exposure
  ``event_impact``         one calendar event, mapped onto your holdings
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from src.utils.logger import get_logger

log = get_logger("llm.insights")

_MAX_TEXT = 8000


def _complete(system: str, prompt: str) -> dict:
    """Run a completion and normalise provider error sentinels into ok=False."""
    try:
        from src.llm import get_llm
        reply = (get_llm().complete(system, prompt) or "").strip()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if not reply:
        return {"ok": False, "error": "The model returned nothing. Check the "
                                      "API key and model in Settings."}
    if reply.lstrip().startswith("["):
        return {"ok": False, "error": reply[:300]}
    # Some reasoning models emit a visible chain-of-thought block first.
    if "</think>" in reply:
        reply = reply.split("</think>", 1)[1].strip()
    return {"ok": True, "text": reply[:_MAX_TEXT],
            "as_of": datetime.now().isoformat(timespec="minutes")}


# ---------------------------------------------------------------------------
# Grounding helpers — compact, factual, best-effort
# ---------------------------------------------------------------------------

def portfolio_context(top_n: int = 12) -> str:
    """Holdings + P&L, as a few compact lines."""
    try:
        from src.portfolio import PortfolioManager
        snap = PortfolioManager().snapshot()
        s = snap.summary
        lines = [
            "PORTFOLIO: invested ₹{:.0f}, current ₹{:.0f}, P&L ₹{:.0f} ({:.2f}%), "
            "day change ₹{:.0f}, {} holdings.".format(
                s.get("holdings_invested", 0), s.get("holdings_value", 0),
                s.get("holdings_pnl", 0), s.get("holdings_pnl_pct", 0),
                s.get("day_change_value", 0), s.get("n_holdings", 0))
        ]
        if not snap.holdings.empty:
            h = snap.holdings.sort_values("current_value", ascending=False)
            total = float(h["current_value"].sum()) or 1.0
            rows = []
            for _, r in h.head(top_n).iterrows():
                rows.append(
                    "{}: {:.1f}% of book, qty {}, avg {}, ltp {}, P&L {:.0f}".format(
                        r.get("tradingsymbol"),
                        float(r.get("current_value", 0)) / total * 100,
                        r.get("quantity"), r.get("average_price"),
                        r.get("last_price"), float(r.get("pnl", 0) or 0)))
            lines.append("HOLDINGS (by weight):\n" + "\n".join(rows))
            if "sector" in h.columns:
                sec = h.groupby("sector")["current_value"].sum().sort_values(ascending=False)
                lines.append("SECTOR MIX: " + ", ".join(
                    f"{k} {v / total * 100:.0f}%" for k, v in sec.head(8).items()))
        return "\n".join(lines)
    except Exception as e:
        return f"PORTFOLIO: unavailable ({e})."


def benchmark_context(perf: Optional[dict]) -> str:
    """The vs-index numbers, if a performance run has produced them."""
    b = (perf or {}).get("benchmark") or {}
    st = b.get("stats") or {}
    if not st:
        return "BENCHMARK: not computed yet (run the performance analysis)."
    name = (b.get("benchmark") or {}).get("name", "index")
    def g(k, suffix="%"):
        v = st.get(k)
        return f"{v}{suffix}" if v is not None else "n/a"
    return (
        f"VS {name} over the last {b.get('window_days', 365)} days "
        f"({b.get('start')} → {b.get('end')}), time-weighted:\n"
        f"  portfolio {g('portfolio_return_pct')} vs index {g('index_return_pct')} "
        f"→ excess {g('excess_pct')}\n"
        f"  beta {g('beta','')} · alpha {g('alpha_pct')} · correlation {g('correlation','')}\n"
        f"  volatility {g('portfolio_vol_pct')} vs {g('index_vol_pct')} · "
        f"tracking error {g('tracking_error_pct')}\n"
        f"  up-capture {g('up_capture_pct')} · down-capture {g('down_capture_pct')}\n"
        f"  max drawdown {g('portfolio_max_drawdown_pct')} vs {g('index_max_drawdown_pct')}")


def calendar_context(cal: Optional[dict], limit: int = 14) -> str:
    """Upcoming events, most imminent first."""
    if not cal or not cal.get("events"):
        return "CALENDAR: no events loaded."
    today = cal.get("today", "")
    rows = [e for e in cal["events"] if e["date"] >= today][:limit]
    if not rows:
        return "CALENDAR: nothing scheduled in the window."
    return "UPCOMING EVENTS:\n" + "\n".join(
        f"  {e['date']} ({e['weekday']}) [{e['importance']}/{e['region']}/{e['certainty']}] "
        f"{e['title']}" for e in rows)


def news_context(cal: Optional[dict], limit: int = 20) -> str:
    items = (cal or {}).get("bulletin") or []
    if not items:
        return "RECENT NEWS: none loaded."
    return "RECENT MARKET NEWS:\n" + "\n".join(
        f"  - {n['title']} ({n.get('source')})" for n in items[:limit])


# ---------------------------------------------------------------------------
# Application 1 — the daily brief
# ---------------------------------------------------------------------------

def daily_brief(cal: Optional[dict] = None, perf: Optional[dict] = None) -> dict:
    """"What matters today, for MY book" — calendar + news, filtered by holdings."""
    if cal is None:
        from src.tools.market_calendar import build_calendar
        cal = build_calendar(days_ahead=21, days_back=4)

    system = (
        "You are the morning strategist for a single Indian-equities investor. "
        "You get their actual holdings, the scheduled macro events ahead, and "
        "the last few days of market news. Your job is to say what THIS person "
        "should pay attention to — not to summarise the news. Be specific: name "
        "their stocks and sectors. Use ONLY the data given; if something isn't "
        "there, say so plainly rather than guessing. Never invent a number, a "
        "price or a date. Events marked 'estimated' are pattern-derived — refer "
        "to them as expected, not confirmed. No disclaimers, no filler.")

    prompt = f"""Today is {cal.get('today')}.

{portfolio_context()}

{benchmark_context(perf)}

{calendar_context(cal)}

{news_context(cal)}

Write a short morning brief in markdown with exactly these sections:

## The one thing
One sentence: the single most important thing for this portfolio right now.

## Events that hit your book
For each of the 3-4 nearest HIGH-importance events: the date, and one line on
which of THEIR holdings or sectors it touches and in which direction. Skip any
event that genuinely doesn't touch this book, and say why it doesn't.

## What the news changed
2-4 bullets. Only news that plausibly moves a name they own or a sector they're
exposed to. Name the holding.

## Watch list
2-3 concrete, checkable things to watch this week (a level, a print, a result).

Keep the whole thing under 350 words."""
    out = _complete(system, prompt)
    out["grounding"] = {
        "events": len(cal.get("events") or []),
        "news": len(cal.get("bulletin") or []),
        "has_benchmark": bool((perf or {}).get("benchmark", {}).get("stats")),
    }
    return out


# ---------------------------------------------------------------------------
# Application 2 — performance review
# ---------------------------------------------------------------------------

def performance_review(perf: dict) -> dict:
    """Explain the performance report: why you're ahead/behind, and what to change."""
    if not perf:
        return {"ok": False, "error": "Run the performance analysis first."}

    s = perf.get("summary") or {}
    b = perf.get("benchmark") or {}

    def _rows(key, n, fields):
        out = []
        for r in (perf.get(key) or [])[:n]:
            out.append("  " + ", ".join(
                f"{f}={r.get(f)}" for f in fields if r.get(f) is not None))
        return "\n".join(out) or "  (none)"

    missed = sum(float(m.get("missed_value") or 0)
                 for m in (perf.get("opportunity_misses") or []))
    monthly = (b.get("monthly") or [])[-12:]
    monthly_str = "\n".join(
        f"  {m['month']}: you {m['portfolio_pct']:+.2f}% vs index {m['index_pct']:+.2f}%"
        for m in monthly) or "  (not available)"

    system = (
        "You are a portfolio analyst reviewing one investor's track record. You "
        "explain performance in terms of what they DID — position sizing, sell "
        "discipline, concentration, risk taken — not market commentary. "
        "Time-weighted return is used throughout, so deposits are already "
        "excluded and differences vs the index are attributable to selection "
        "and sizing. Use ONLY the numbers given; quote them exactly. If a number "
        "isn't provided, say it isn't available. Be direct about mistakes.")

    prompt = f"""PERFORMANCE REPORT

Invested ₹{s.get('total_invested')}, current value ₹{s.get('current_value')},
P&L ₹{s.get('total_pnl')} ({s.get('total_pnl_pct')}%), XIRR {perf.get('xirr')}%,
{s.get('total_trades')} trades across {s.get('n_holdings')} holdings since {s.get('first_trade_date')}.

{benchmark_context(perf)}

MONTH BY MONTH (time-weighted, vs index):
{monthly_str}

TOP WINNERS:
{_rows('winners', 6, ['tradingsymbol', 'symbol', 'pnl', 'pnl_pct'])}

TOP LOSERS:
{_rows('losers', 6, ['tradingsymbol', 'symbol', 'pnl', 'pnl_pct'])}

SOLD TOO EARLY (fully exited, worth more now) — ₹{missed:.0f} left on the table:
{_rows('opportunity_misses', 6, ['symbol', 'avg_sell_price', 'current_price', 'missed_pct', 'missed_value'])}

Write a review in markdown with these sections:

## Verdict
Two sentences: are you beating the index, and is the extra return worth the
extra risk you took to get it? Cite beta, volatility and drawdown.

## Where the return came from
What actually drove the result — concentration, a few winners, or broad
selection? Reference specific names and the monthly pattern.

## What's costing you
The 2-3 most expensive habits visible in this data. Be concrete: if the sold-
too-early number is large relative to total P&L, say what that implies about
sell discipline. If losers are being held while winners are cut, say so.

## Do this next
3 specific, actionable changes. No generic advice.

Under 400 words."""
    return _complete(system, prompt)


# ---------------------------------------------------------------------------
# Application 3 — risk review
# ---------------------------------------------------------------------------

def risk_review(perf: Optional[dict] = None, cal: Optional[dict] = None) -> dict:
    """Concentration, factor and event exposure — a pre-mortem on the book."""
    risk_str = "RISK FLAGS: not available."
    try:
        from src.portfolio import PortfolioManager
        pm = PortfolioManager()
        snap = pm.snapshot()
        conc = pm.concentration_risk(snap, 15.0)
        under = pm.underperformers(snap, -10.0)
        bits = []
        if not conc.empty:
            bits.append("over-weight (>15% of book): " + ", ".join(
                f"{r.tradingsymbol} {r.weight_pct}%" for r in conc.itertuples()))
        else:
            bits.append("no single holding exceeds 15% of the book")
        if not under.empty:
            bits.append("down more than 10%: " + ", ".join(
                f"{r.tradingsymbol} {r.pnl_pct}%" for r in under.head(8).itertuples()))
        risk_str = "RISK FLAGS: " + "; ".join(bits) + "."
    except Exception as e:
        log.debug(f"risk flags unavailable: {e}")

    system = (
        "You are a risk manager reviewing a single retail equity portfolio. You "
        "look for the ways this book actually breaks: concentration in one name "
        "or sector, hidden single-factor bets (everything long the same macro "
        "driver), and calendar events that would hit several holdings at once. "
        "Use ONLY the data given. Quantify every claim with a number from the "
        "data. Do not pad with generic risk-management advice.")

    prompt = f"""{portfolio_context(top_n=15)}

{risk_str}

{benchmark_context(perf)}

{calendar_context(cal, limit=10)}

Write a risk review in markdown:

## Biggest exposure
The single largest concentration risk, with the percentage.

## Correlated bets
Which holdings would fall together, and on what shared driver (a rate move, the
rupee, crude, one sector). Name them.

## Event risk
Which upcoming calendar events would hit more than one holding at once.

## Three fixes
Ranked, specific, with the approximate size of each trim or add.

Under 300 words."""
    return _complete(system, prompt)


# ---------------------------------------------------------------------------
# Application 4 — one event, mapped onto the book
# ---------------------------------------------------------------------------

def event_impact(event: dict) -> dict:
    """"What does this specific event mean for me?" — for a calendar row tap."""
    if not event or not event.get("title"):
        return {"ok": False, "error": "No event supplied."}

    system = (
        "You explain one scheduled market event to an investor in terms of their "
        "own holdings. Use ONLY the holdings data provided. Be concrete about "
        "direction and mechanism — which holding, via what channel (rates, the "
        "rupee, input costs, demand, flows). Do not predict the outcome of the "
        "event itself; explain the transmission both ways. If the event is "
        "marked 'estimated', note that the date is expected, not confirmed.")

    prompt = f"""EVENT
{event.get('date')} ({event.get('weekday')}) — {event.get('title')}
Region: {event.get('region')} · Importance: {event.get('importance')} ·
Date certainty: {event.get('certainty')} (source: {event.get('source')})
Standing note: {event.get('why')}

{portfolio_context(top_n=15)}

Answer in markdown, under 220 words:

## What it is
One or two sentences, in plain language.

## Your exposure
The specific holdings that react, and through what channel.

## If it surprises hawkish / negative
What likely happens to those names.

## If it surprises dovish / positive
What likely happens to those names.

## Positioning
One sentence: is this worth acting on before the date, or just watching?"""
    out = _complete(system, prompt)
    out["event"] = {k: event.get(k) for k in ("date", "title", "region", "importance")}
    return out

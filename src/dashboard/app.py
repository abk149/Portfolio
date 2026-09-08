"""FastAPI dashboard.

Run with:
    python main.py dashboard           # → http://127.0.0.1:8000

Every section of the dashboard is just a button that hits one of the JSON
endpoints below. Heavy operations (full-universe scan, MPT, agent calls) are
launched as background jobs so the UI never blocks — the frontend polls
`/api/jobs/{id}` until status == done.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from src.utils.logger import get_logger

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import settings

HERE = Path(__file__).resolve().parent

app = FastAPI(title="Upstox Portfolio Dashboard")

app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


def _build_info() -> dict:
    """Which code is actually running.

    Answers the question that otherwise costs a full rebuild-and-ask cycle:
    is this a regression, or an old install?
    """
    try:
        from src import _build_info as bi
        return {"id": getattr(bi, "BUILD_ID", "?"),
                "built_at": getattr(bi, "BUILT_AT", "?")}
    except Exception:
        return {"id": "unknown", "built_at": "unknown"}


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """Turn any unhandled error into a clean JSON message.

    Without this, an exception anywhere becomes a raw ASGI traceback: the
    terminal fills with fifty frames of starlette internals and the app shows a
    truncated "HTTP 500: File ..." with the actual cause cut off. The full
    traceback still goes to the log, where it belongs.
    """
    import traceback
    log = get_logger("dashboard")
    log.error(f"unhandled error on {request.url.path}: "
              f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={
            "error": f"{type(exc).__name__}: {exc}"[:400],
            "path": request.url.path,
            # Stamped so a screenshot of an error is self-identifying — no more
            # guessing whether the device has the fix for it.
            "build": _build_info(),
            "hint": "Full traceback is in the System Terminal log.",
        },
    )


# (Heavy modules are no longer preloaded — quant runs use a subprocess so the
# dashboard process stays lean.)

# ---------------- job runner (so the UI never blocks) ----------------
JOBS: dict[str, dict] = {}
POOL = ThreadPoolExecutor(max_workers=4)


def _run_job(job_id: str, fn, *args, **kwargs):
    import sys
    import traceback as _tb
    JOBS[job_id] = {"status": "running", "result": None, "error": None}

    def _exec():
        print(f"[POOL] job={job_id} thread started", flush=True, file=sys.stderr)
        try:
            JOBS[job_id]["result"] = fn(*args, **kwargs)
            JOBS[job_id]["status"] = "done"
            print(f"[POOL] job={job_id} done", flush=True, file=sys.stderr)
        except Exception as e:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = f"{type(e).__name__}: {e}"
            print(f"[POOL] job={job_id} ERROR {e}", flush=True, file=sys.stderr)
            print(_tb.format_exc(), flush=True, file=sys.stderr)

    POOL.submit(_exec)
    return job_id


def _df(o):
    """DataFrame → JSON-safe list of records.

    The obvious `clean.where(pd.notnull(clean), None)` does NOT work, which is
    what this used to do: you cannot store None in a float64 column, so pandas
    silently coerces it straight back to NaN. Non-float columns were cleaned and
    float columns — exactly the ones that carry missing fundamentals — were not,
    so any screener result with a missing P/E or ROE reached Starlette as NaN
    and 500'd with "Out of range float values are not JSON compliant".

    Scrubbing the records after conversion is the reliable order, and it picks
    up numpy scalars (np.int64, np.bool_) in the same pass.
    """
    if isinstance(o, pd.DataFrame):
        return _scrub_for_json(o.to_dict("records"))
    return _scrub_for_json(o)


# ---------------- views ----------------
@app.get("/", response_class=HTMLResponse)
def index():
    return (HERE / "templates" / "index.html").read_text()


# ---------------- portfolio ----------------
def _need_upstox():
    # Tuple so `except _need_upstox()` catches BOTH the Upstox auth error and
    # the generic broker auth error (Groww), regardless of active broker.
    from src.upstox.client import UpstoxAuthError
    from src.brokers.base import BrokerAuthError
    return (UpstoxAuthError, BrokerAuthError)


@app.get("/api/portfolio")
def api_portfolio():
    from src.portfolio import PortfolioManager
    try:
        snap = PortfolioManager().snapshot()
    except _need_upstox() as e:
        return JSONResponse(
            {"error": "upstox_not_authenticated", "message": str(e),
             "hint": "Run /upstox_login on Telegram, or:  python -m src.upstox.auth"},
            status_code=200,
        )
    return _scrub_for_json({
        "summary": snap.summary,
        "holdings": _df(snap.holdings),
        "positions": _df(snap.positions),
        "allocation": _df(snap.allocation),
    })


@app.get("/api/portfolio/risk")
def api_portfolio_risk(threshold: float = 15.0):
    from src.portfolio import PortfolioManager
    try:
        pm = PortfolioManager()
        snap = pm.snapshot()
    except _need_upstox() as e:
        return JSONResponse(
            {"error": "upstox_not_authenticated", "message": str(e)},
            status_code=200,
        )
    return {
        "concentration": _df(pm.concentration_risk(snap, threshold)),
        "underperformers": _df(pm.underperformers(snap, -10)),
    }


@app.post("/api/portfolio/deploy-cash")
def api_portfolio_deploy_cash(body: dict):
    """Given an amount of new cash, return BUY-ONLY allocation that pushes
    the portfolio toward the efficient frontier (max-Sharpe constrained on
    no-sells)."""
    from src.portfolio import PortfolioManager, PortfolioOptimizer

    try:
        cash = float(body.get("cash") or 0)
    except (TypeError, ValueError):
        cash = 0
    if cash <= 0:
        return {"error": "cash must be > 0"}

    include_universe = bool(body.get("include_universe", True))
    universe = body.get("universe", "all_nse")
    max_weight = float(body.get("max_weight", 0.25))

    try:
        pm = PortfolioManager()
        snap = pm.snapshot()
    except _need_upstox() as e:
        # Every sibling endpoint handles this; this one didn't, so an expired
        # token surfaced as a 500 with a wall of traceback.
        return {"error": "Broker not authenticated — open Login and reconnect. "
                         f"({e})"}
    if snap.holdings.empty:
        return {"error": "No holdings to optimise around. This suggests an "
                         "allocation relative to what you already own, so it "
                         "needs at least one existing position."}

    val_by_yf = {}
    for _, row in snap.holdings.iterrows():
        sym = row.get("tradingsymbol", "")
        yf = f"{sym}.NS"
        val_by_yf[yf] = float(row.get("current_value", 0))

    # Explicit ticker list (e.g. from the macro Ideas tab) takes priority.
    candidates: list[str] = []
    for t in (body.get("tickers") or []):
        sym = str(t).upper().strip().replace(".NS", "").replace(".BO", "")
        if sym:
            candidates.append(f"{sym}.NS")

    # Pull STRONG_BUY candidates from the KB universe store
    if include_universe:
        try:
            from src.kb import KnowledgeBase
            kb = KnowledgeBase.get()
            for stock in kb.all_stocks():
                reco = stock.get("recommendation", "")
                if reco in ("STRONG_BUY", "BUY"):
                    sym = (stock.get("symbol") or "").upper()
                    if sym:
                        candidates.append(f"{sym}.NS")
            candidates = candidates[:25]
        except Exception as e:
            log = get_logger("dashboard")
            log.debug(f"universe candidates pull failed: {e}")

    try:
        res = PortfolioOptimizer().deploy_cash(
            current_value_by_yf=val_by_yf,
            cash_to_deploy=cash,
            candidates_extra=candidates,
            max_weight=max_weight,
        )
    except Exception as e:
        get_logger("dashboard").exception("deploy_cash failed")
        return {"error": f"Could not compute an allocation: {type(e).__name__}: {e}",
                "hint": "This needs a year of overlapping price history for your "
                        "holdings. If the broker feed is down it falls back to a "
                        "free public source, which can be rate-limited — retry in "
                        "a minute."}
    if res.get("error"):
        return res
    res["universe_candidates_considered"] = len(candidates)
    _capture_recommendations("optimizer", [
        {"symbol": (b.get("ticker") or "").replace(".NS", "").replace(".BO", ""),
         "suggested_amount": b.get("buy_inr"),
         "suggested_shares": b.get("shares"),
         "suggested_entry": b.get("price"),
         "rationale": ("New position — the optimiser wants "
                       f"{b.get('final_weight_pct')}% of the book here."
                       if b.get("is_new_position") else
                       f"Top-up to {b.get('final_weight_pct')}% of the book.")}
        for b in (res.get("buys") or [])
    ])

    # Enrich each buy with a live price and a whole-share count (amount → shares).
    try:
        from src.data import MarketData
        md = MarketData()
        for b in res.get("buys", []):
            px = md.ltp(b.get("ticker", ""))
            if px and px > 0:
                b["price"] = round(float(px), 2)
                b["shares"] = int(b.get("buy_inr", 0) // px)
    except Exception as e:
        get_logger("dashboard").debug(f"deploy-cash share enrich failed: {e}")
    return _scrub_for_json(res)


@app.post("/api/portfolio/upload_trades")
async def api_portfolio_upload_trades(file: UploadFile = File(...)):
    if not file.filename.endswith((".csv", ".xlsx", ".xls")):
        raise HTTPException(400, "File must be CSV or Excel.")
    
    # Save file
    cache_dir = Path(".cache/user_trades")
    cache_dir.mkdir(parents=True, exist_ok=True)
    file_path = cache_dir / file.filename
    
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)
        
    # Attempt parsing to validate
    from src.portfolio.analytics import PerformanceAnalyzer
    try:
        pa = PerformanceAnalyzer()
        df = pa._load_user_uploaded_trades(str(file_path))
        return {
            "message": "File uploaded and parsed successfully.",
            "trades_found": len(df)
        }
    except Exception as e:
        # if it fails, delete the file so it doesn't break future runs
        if file_path.exists():
            file_path.unlink()
        raise HTTPException(400, f"Error parsing file: {str(e)}")

@app.post("/api/portfolio/optimize")
def api_portfolio_optimize(body: dict):
    job_id = uuid.uuid4().hex[:8]

    def _do():
        import numpy as np
        import pandas as pd
        from src.portfolio import PortfolioManager, PortfolioOptimizer
        pm, opt = PortfolioManager(), PortfolioOptimizer()
        snap = pm.snapshot()
        if snap.holdings.empty:
            return {"error": "No holdings."}
        tickers, val = [], {}
        for _, row in snap.holdings.iterrows():
            sym = row.get("tradingsymbol", "")
            yf = f"{sym}.NS"
            # Upstox holdings response uses 'instrument_token' historically but
            # some accounts return 'instrument_key'. We try both, then fall back
            # to auto-resolution inside MarketData.
            ikey = row.get("instrument_key") or row.get("instrument_token") or ""
            tickers.append((yf, ikey))
            val[yf] = float(row.get("current_value", 0))
        rets = opt.returns(tickers, lookback_days=body.get("lookback_days", 365))
        if rets.empty or rets.shape[1] < 2:
            return {"error": "Insufficient overlapping history."}
        mode = body.get("mode", "max_sharpe")
        mw = body.get("max_weight", 0.25)
        if mode == "min_variance":
            res = opt.min_variance(rets, max_weight=mw)
        elif mode == "target_return":
            res = opt.target_return(rets, target=body.get("target", 0.20), max_weight=mw)
        else:
            res = opt.max_sharpe(rets, max_weight=mw)
        frontier = opt.efficient_frontier(rets, points=20, max_weight=mw)
        rebal = opt.rebalance_suggestion(val, res)

        # Where does the CURRENT portfolio sit on the (vol, return) plane?
        total = sum(v for v in val.values() if v > 0)
        cur_point = None
        per_name = []
        if total > 0:
            mu_ann = rets.mean() * 252
            cov_ann = rets.cov() * 252
            # weights of CURRENT portfolio in the same column space as rets
            w = pd.Series({t: v / total for t, v in val.items()
                          if t in rets.columns and v > 0})
            w = w.reindex(rets.columns).fillna(0.0)
            if w.sum() > 0:
                w = w / w.sum()
                cur_ret = float(w.values @ mu_ann.values)
                cur_vol = float(np.sqrt(w.values @ cov_ann.values @ w.values))
                cur_sharpe = (cur_ret - opt.rf) / cur_vol if cur_vol > 0 else 0.0
                cur_point = {
                    "return": cur_ret,
                    "vol": cur_vol,
                    "sharpe": round(cur_sharpe, 3),
                    "return_pct": round(cur_ret * 100, 2),
                    "vol_pct": round(cur_vol * 100, 2),
                }
                # also surface each holding as its own dot for context
                for t in rets.columns:
                    pn_ret = float(mu_ann[t])
                    pn_vol = float(np.sqrt(cov_ann.loc[t, t]))
                    per_name.append({
                        "ticker": t,
                        "return": pn_ret, "vol": pn_vol,
                        "weight_pct": round(float(w[t]) * 100, 2),
                    })

        return _scrub_for_json({
            "mode": mode,
            "expected_return_pct": round(res.expected_return * 100, 2),
            "volatility_pct": round(res.volatility * 100, 2),
            "sharpe": round(res.sharpe, 3),
            "weights": [{"ticker": k, "weight_pct": round(v * 100, 2)}
                        for k, v in res.weights.items()],
            "rebalance": _df(rebal.reset_index().rename(columns={"index": "ticker"})),
            "frontier": _df(frontier),
            "current_portfolio": cur_point,
            "per_name": per_name,
        })

    _run_job(job_id, _do)
    return {"job_id": job_id}


# ---------------- portfolio performance ----------------
_PERF_CACHE: dict[str, Any] = {"data": None}


@app.post("/api/portfolio/performance")
def api_portfolio_performance():
    """Run the full performance analysis as a background job."""
    job_id = uuid.uuid4().hex[:8]

    def _do():
        from src.portfolio import PerformanceAnalyzer
        result = PerformanceAnalyzer().full_report()
        _PERF_CACHE["data"] = result
        return _scrub_for_json(result)

    _run_job(job_id, _do)
    return {"job_id": job_id}


@app.get("/api/portfolio/performance/cached")
def api_portfolio_performance_cached():
    """Return cached performance data (avoids re-computation on tab switch)."""
    if _PERF_CACHE["data"]:
        return {"ok": True, "data": _scrub_for_json(_PERF_CACHE["data"])}
    return {"ok": False, "error": "No cached performance data. Click Analyze first."}


# ---------------- benchmark (vs index) ----------------
@app.post("/api/portfolio/benchmark")
def api_portfolio_benchmark(body: dict | None = None):
    """Time-weighted portfolio return vs a market index.

    Runs off the cached performance report's equity curve, so it's cheap and
    instant once performance has been analysed once.
    """
    body = body or {}
    index = body.get("index") or body.get("benchmark") or "^NSEI"
    days = int(body.get("window_days") or 365)
    perf = _PERF_CACHE.get("data")
    if not perf or not perf.get("equity_curve"):
        return {"ok": False, "error": "No portfolio history yet. Run "
                                      "'Analyse performance' first."}
    try:
        from src.portfolio.benchmark import compare
        res = compare(pd.DataFrame(perf["equity_curve"]),
                      benchmark=index, window_days=days)
        if res.get("error"):
            return {"ok": False, **res}
        # Keep the cached report in step so the AI review sees the same numbers.
        perf["benchmark"] = res
        return {"ok": True, **_scrub_for_json(res)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ---------------- recommendations → ghost portfolio ----------------
def _capture_recommendations(source: str, items: list[dict],
                             run_id: str | None = None) -> None:
    """Record what an engine just suggested. Never let this break the engine."""
    try:
        from src.portfolio.recommendations import record
        record([i for i in items if i.get("symbol")], source, run_id)
    except Exception as e:
        get_logger("dashboard").debug(f"recommendation capture failed: {e}")


def _capture_quant(result: dict) -> dict:
    """Record a DR-Quant run's validated names.

    Called from BOTH run paths. The desktop spawns a subprocess and the phone
    runs it in a thread, and the first version of this hooked only the
    subprocess branch — so on the device, which is where it matters, DR-Quant
    picks never reached the ghost queue at all.
    """
    try:
        _capture_recommendations("dr-quant", [
            {"symbol": v.get("symbol") or v.get("ticker"),
             "sector": v.get("sector"),
             "conviction": ("HIGH" if (v.get("health_score") or 0) >= 70
                            else "MEDIUM"),
             "rationale": v.get("thesis")}
            for v in ((result or {}).get("validated") or [])
        ], run_id=(result or {}).get("run_id"))
    except Exception as e:
        get_logger("dashboard").debug(f"quant capture failed: {e}")
    return result


@app.get("/api/recommendations")
def api_recommendations(status: str | None = None):
    """Everything the engines have suggested, newest first."""
    from src.portfolio.recommendations import list_all
    return _scrub_for_json(list_all(status))


@app.post("/api/recommendations/take")
def api_recommendation_take(body: dict):
    """Buy a recommendation into the ghost book, keeping its provenance."""
    from src.portfolio.ghost import buy
    from src.portfolio.recommendations import list_all, set_status

    rec_id = body.get("id", "")
    rec = next((i for i in list_all()["items"] if i["id"] == rec_id), None)
    if not rec:
        return {"error": "Recommendation not found."}

    amount = body.get("amount") or rec.get("suggested_amount")
    if not amount:
        return {"error": "No amount given, and this recommendation didn't "
                         "suggest one — enter how much to put in."}
    res = buy(symbol=rec["symbol"], amount=float(amount),
              source=rec["source"],
              note=f"{rec['source_label']} · {(rec.get('rationale') or '')[:120]}")
    if res.get("error"):
        return res
    set_status(rec_id, "taken", ghost_id=res["position"]["id"])
    return _scrub_for_json(res)


@app.post("/api/recommendations/dismiss")
def api_recommendation_dismiss(body: dict):
    from src.portfolio.recommendations import set_status
    return _scrub_for_json(set_status(body.get("id", ""), "dismissed"))


@app.post("/api/recommendations/clear")
def api_recommendations_clear(body: dict | None = None):
    from src.portfolio.recommendations import clear
    return clear((body or {}).get("which", "dismissed"))


# ---------------- ghost (paper) portfolio ----------------
@app.get("/api/ghost")
def api_ghost():
    """Open + closed paper positions, marked to market, split by engine."""
    from src.portfolio.ghost import snapshot
    snap = snapshot()
    try:
        from src.portfolio.recommendations import attribution
        snap["attribution"] = attribution(snap)
    except Exception as e:
        get_logger("dashboard").debug(f"attribution failed: {e}")
    return _scrub_for_json(snap)


@app.post("/api/ghost/buy")
def api_ghost_buy(body: dict):
    """Take a paper position at the prevailing price."""
    from src.portfolio.ghost import buy
    return _scrub_for_json(buy(
        symbol=body.get("symbol", ""),
        amount=body.get("amount"),
        qty=body.get("qty"),
        note=body.get("note", ""),
        source=body.get("source", "manual"),
    ))


@app.post("/api/ghost/sell")
def api_ghost_sell(body: dict):
    from src.portfolio.ghost import sell
    return _scrub_for_json(sell(body.get("id", "")))


@app.post("/api/ghost/reset")
def api_ghost_reset():
    from src.portfolio.ghost import reset
    return reset()


@app.post("/api/ghost/curve")
def api_ghost_curve():
    """Ghost curve alone, and combined with the real book (background job)."""
    job_id = uuid.uuid4().hex[:8]

    def _do():
        from src.portfolio.ghost import combined_curve
        perf = _PERF_CACHE.get("data") or {}
        return _scrub_for_json(combined_curve(perf.get("equity_curve")))

    _run_job(job_id, _do)
    return {"job_id": job_id}


@app.post("/api/ghost/review")
def api_ghost_review(body: dict | None = None):
    """Sell discipline: computed exit signals + an LLM call on top."""
    body = body or {}
    job_id = uuid.uuid4().hex[:8]

    def _do():
        from src.portfolio.ghost import signals
        cal = _CAL_CACHE.get("data")
        if cal is None:
            try:
                cal = _calendar(days_ahead=30, days_back=2)
            except Exception as e:
                get_logger("dashboard").debug(f"ghost review calendar failed: {e}")
        sig = signals(calendar=cal)
        out = {"signals": _scrub_for_json(sig)}
        if body.get("with_ai", True):
            from src.llm.insights import ghost_review
            out["ai"] = ghost_review(sig, cal)
        return out

    _run_job(job_id, _do)
    return {"job_id": job_id}


# ---------------- news source diagnostics ----------------
@app.get("/api/news/health")
def api_news_health(refresh: bool = False):
    """Per-source status for the news backbone, plus Reddit's gate state.

    Exists so a degraded feed is visible in the UI instead of silently shrinking
    the evidence pool — a source that returns nothing looks exactly like "no
    news today" unless something says otherwise.
    """
    out: dict = {"ok": True}
    try:
        from src.tools.news_sources import fetch_all, health_report
        if refresh:
            fetch_all(limit_per_source=4, days=3)
        out["news"] = health_report()
    except Exception as e:
        out["ok"] = False
        out["news"] = {"error": f"{type(e).__name__}: {e}"}
    try:
        from src.tools.reddit import health as reddit_health
        out["reddit"] = reddit_health()
    except Exception as e:
        out["reddit"] = {"available": False, "last_error": str(e)}
    return _scrub_for_json(out)


# ---------------- market calendar ----------------
_CAL_CACHE: dict = {"data": None, "at": 0.0}


def _calendar(days_ahead: int = 60, days_back: int = 7, max_age_s: int = 1800) -> dict:
    """Build the calendar, cached briefly — it makes ~20 network calls."""
    import time
    now = time.time()
    cached = _CAL_CACHE.get("data")
    if cached and (now - _CAL_CACHE.get("at", 0)) < max_age_s:
        return cached
    from src.tools.market_calendar import build_calendar
    data = build_calendar(days_ahead=days_ahead, days_back=days_back)
    _CAL_CACHE["data"] = data
    _CAL_CACHE["at"] = now
    return data


@app.post("/api/calendar")
def api_calendar(body: dict | None = None):
    """Upcoming market-moving events + a recent news bulletin (background job).

    Job-based because it scrapes the Fed calendar, RBI and ~18 news feeds.
    """
    body = body or {}
    days_ahead = int(body.get("days_ahead") or 60)
    days_back = int(body.get("days_back") or 7)
    refresh = bool(body.get("refresh"))
    job_id = uuid.uuid4().hex[:8]

    def _do():
        if refresh:
            _CAL_CACHE["data"] = None
        return _scrub_for_json(_calendar(days_ahead, days_back))

    _run_job(job_id, _do)
    return {"job_id": job_id}


@app.get("/api/calendar/cached")
def api_calendar_cached():
    if _CAL_CACHE.get("data"):
        return {"ok": True, "data": _scrub_for_json(_CAL_CACHE["data"])}
    return {"ok": False, "error": "No calendar loaded yet."}


# ---------------- AI applications ----------------
@app.post("/api/ai/brief")
def api_ai_brief():
    """Morning brief: the calendar and the news, filtered through YOUR holdings."""
    job_id = uuid.uuid4().hex[:8]

    def _do():
        from src.llm.insights import daily_brief
        return daily_brief(cal=_calendar(days_ahead=21, days_back=4),
                           perf=_PERF_CACHE.get("data"))

    _run_job(job_id, _do)
    return {"job_id": job_id}


@app.post("/api/ai/performance-review")
def api_ai_performance_review():
    """Narrative review of the performance report + benchmark comparison."""
    perf = _PERF_CACHE.get("data")
    if not perf:
        return {"ok": False, "error": "Run 'Analyse performance' first — the "
                                      "review is built from that report."}
    job_id = uuid.uuid4().hex[:8]

    def _do():
        from src.llm.insights import performance_review
        return performance_review(perf)

    _run_job(job_id, _do)
    return {"job_id": job_id}


@app.post("/api/ai/risk-review")
def api_ai_risk_review():
    """Concentration / correlated-bet / event-risk pre-mortem on the book."""
    job_id = uuid.uuid4().hex[:8]

    def _do():
        from src.llm.insights import risk_review
        return risk_review(perf=_PERF_CACHE.get("data"),
                           cal=_CAL_CACHE.get("data"))

    _run_job(job_id, _do)
    return {"job_id": job_id}


@app.post("/api/ai/event-impact")
def api_ai_event_impact(body: dict):
    """What one calendar event means for this specific portfolio."""
    event = body.get("event") or {}
    if not event.get("title"):
        # Allow addressing an event by date+title from the cached calendar.
        cal = _CAL_CACHE.get("data") or {}
        want_date, want_title = body.get("date"), body.get("title")
        for e in (cal.get("events") or []):
            if e.get("date") == want_date and e.get("title") == want_title:
                event = e
                break
    if not event.get("title"):
        return {"ok": False, "error": "Event not found. Load the calendar first."}
    job_id = uuid.uuid4().hex[:8]

    def _do():
        from src.llm.insights import event_impact
        return event_impact(event)

    _run_job(job_id, _do)
    return {"job_id": job_id}


# ---------------- screener ----------------
@app.post("/api/screener/scan")
def api_screener(body: dict):
    job_id = uuid.uuid4().hex[:8]

    def _do():
        from src.screener import ScreenerEngine
        eng = ScreenerEngine()
        tech = eng.technical_scan(body.get("universe", "nifty50"),
                                  tech_min=body.get("tech_min", 60.0))
        full = eng.fundamental_scan(tech, fund_min=body.get("fund_min", 50.0)) \
            if not tech.empty else pd.DataFrame()
        return _scrub_for_json({
            "technical_count": len(tech),
            "final_count": len(full),
            "technical": _df(tech.head(100)),
            "results": _df(full),
        })

    _run_job(job_id, _do)
    return {"job_id": job_id}


# ---------------- intraday ----------------
@app.post("/api/intraday/analyze")
def api_intraday_analyze(body: dict):
    job_id = uuid.uuid4().hex[:8]

    def _do():
        from src.intraday import IntradayAnalyzer
        return IntradayAnalyzer().analyze(body.get("days", 60))

    _run_job(job_id, _do)
    return {"job_id": job_id}


@app.post("/api/intraday/scan")
def api_intraday_scan(body: dict):
    job_id = uuid.uuid4().hex[:8]

    def _do():
        from src.intraday import IntradayScanner
        df = IntradayScanner().scan(body.get("universe", "nifty50"),
                                    min_score=body.get("min_score", 40))
        return {"count": len(df), "rows": _df(df)}

    _run_job(job_id, _do)
    return {"job_id": job_id}


# ---------------- agent ----------------
class AgentBody(BaseModel):
    agent: str
    question: str


@app.post("/api/agent")
def api_agent(body: AgentBody):
    job_id = uuid.uuid4().hex[:8]

    def _do():
        from src.agents import IntradayAgent, PortfolioAgent, ScreenerAgent
        cls = {"portfolio": PortfolioAgent, "screener": ScreenerAgent, "intraday": IntradayAgent}[body.agent]
        return {"answer": cls().run(body.question)}

    _run_job(job_id, _do)
    return {"job_id": job_id}


@app.post("/api/deep-dive")
def api_deep_dive(body: dict):
    """Per-stock deep dive: last-2-quarter results + valuation issues + a quant
    entry-price zone. Job-based (web + PDF + LLM = slow)."""
    job_id = uuid.uuid4().hex[:8]
    symbol = str(body.get("symbol") or "").upper().strip()
    if not symbol:
        return {"job_id": None, "error": "symbol required"}

    def _do():
        from src.tools.deep_dive import deep_dive
        return deep_dive(symbol)

    _run_job(job_id, _do)
    return {"job_id": job_id}


@app.post("/api/themes")
def api_themes(body: dict):
    """Macro-infused theme ideas: ingest recent macro/news/Reddit + Universe Map
    + DR-Quant → themes mapped to stocks. Job-based (web + LLM = slow)."""
    job_id = uuid.uuid4().hex[:8]
    days = int(body.get("days", 14))
    max_themes = int(body.get("max_themes", 6))

    def _do():
        from src.tools.macro_intel import build_macro_themes
        res = build_macro_themes(days=days, max_themes=max_themes)
        _capture_recommendations("macro-ideas", [
            {"symbol": p.get("symbol"), "sector": p.get("sector"),
             "conviction": p.get("conviction"), "rationale": p.get("thesis"),
             "suggested_entry": (p.get("entry") or {}).get("suggested_entry")}
            for p in (res.get("picks") or [])
        ], run_id=res.get("as_of"))
        return res

    _run_job(job_id, _do)
    return {"job_id": job_id}


# Curated NVIDIA models for portfolio/finance work, ordered best-first.
# Latencies measured live against integrate.api.nvidia.com (first token → done).
# NOTE: the /v1/models catalog lists models your key may NOT be entitled to
# (they answer 404 "Not found for account"), so this shortlist is what we've
# actually verified end-to-end. Use "Test LLM connection" after switching.
LLM_RECOMMENDED = [
    {"id": "nvidia/nemotron-3-super-120b-a12b",
     "label": "Nemotron 3 Super 120B — recommended",
     "note": "Fast (~0.7s) + strong reasoning. Best all-round default."},
    {"id": "nvidia/nemotron-3-ultra-550b-a55b",
     "label": "Nemotron 3 Ultra 550B — deepest analysis",
     "note": "~10s to first token. Sharpest financial judgement; great for Ideas & deep dives."},
    {"id": "openai/gpt-oss-20b",
     "label": "GPT-OSS 20B — fast, clean JSON",
     "note": "~1.7s. Reliable structured output, lighter reasoning."},
    {"id": "nvidia/nemotron-3.5-lightning-30b-a3b",
     "label": "Nemotron 3.5 Lightning 30B — fastest",
     "note": "~0.7s. Cheapest/quickest; can be chattier around JSON."},
    {"id": "deepseek-ai/deepseek-v4-pro-0813",
     "label": "DeepSeek V4 Pro (slow)",
     "note": "Did not answer within 70s in testing — raise NVIDIA_TIMEOUT to try."},
    {"id": "moonshotai/kimi-k3",
     "label": "Kimi K3 (very slow — not advised)",
     "note": "Measured ~240-300s to first token. Works via streaming but too slow for multi-call flows."},
]


@app.get("/api/llm/models")
def api_llm_models():
    """Model picker data: the curated shortlist + the live catalog for the
    configured key. `available` marks catalog ids so the UI can flag unknowns."""
    settings.refresh()
    catalog: list[str] = []
    err = None
    key = settings.nvidia_api_key
    if key:
        try:
            import requests as _rq
            r = _rq.get(settings.nvidia_base_url.rstrip("/") + "/models",
                        headers={"Authorization": f"Bearer {key}"}, timeout=20)
            if r.status_code == 200:
                catalog = sorted(m.get("id", "") for m in r.json().get("data", []))
            else:
                err = f"HTTP {r.status_code}: {r.text[:160]}"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
    else:
        err = "NVIDIA_API_KEY not set"
    return {"ok": err is None, "error": err,
            "current": settings.nvidia_model,
            "recommended": LLM_RECOMMENDED,
            "catalog": catalog}


@app.post("/api/llm/config")
def api_llm_config(body: dict):
    """Push the LLM key/model into the running process (no restart)."""
    def _clean(v):
        return (str(v or "")).strip().strip('"').strip("'").strip()
    key = _clean(body.get("api_key"))
    model = _clean(body.get("model"))
    if key:
        os.environ["NVIDIA_API_KEY"] = key
    if model:
        os.environ["NVIDIA_MODEL"] = model
    try:
        settings.refresh()
    except Exception:
        pass
    return {"ok": True, "model": settings.nvidia_model,
            "have_key": bool(settings.nvidia_api_key)}


@app.post("/api/llm/test")
def api_llm_test():
    """Quick connectivity check for the configured LLM (NVIDIA → fallback chain)."""
    try:
        from src.llm import get_llm
        llm = get_llm()
        reply = (llm.complete("You are a connectivity test.",
                              "Reply with exactly: OK") or "").strip()
        provider = getattr(llm, "name", None) or settings.llm_provider
        # Providers return sentinels like "[nvidia error …]" on failure.
        ok = bool(reply) and not reply.lstrip().startswith("[")
        return {"ok": ok, "provider": provider, "reply": reply[:300]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _chat_context() -> str:
    """Compact snapshot of everything the AI should know: portfolio, the latest
    DR-Quant run, and the Universe-Map / KB. Every piece is best-effort."""
    parts: list[str] = []
    # Portfolio
    try:
        from src.portfolio import PortfolioManager
        snap = PortfolioManager().snapshot()
        s = snap.summary
        parts.append(
            "PORTFOLIO: invested ₹{:.0f}, current ₹{:.0f}, P&L ₹{:.0f} ({:.2f}%), "
            "day change ₹{:.0f}, {} holdings.".format(
                s.get("holdings_invested", 0), s.get("holdings_value", 0),
                s.get("holdings_pnl", 0), s.get("holdings_pnl_pct", 0),
                s.get("day_change_value", 0), s.get("n_holdings", 0)))
        if not snap.holdings.empty:
            top = snap.holdings.sort_values("current_value", ascending=False).head(12)
            rows = [f"{r.get('tradingsymbol')}: qty {r.get('quantity')}, "
                    f"avg {r.get('average_price')}, ltp {r.get('last_price')}, "
                    f"P&L {round(r.get('pnl', 0), 0)}" for _, r in top.iterrows()]
            parts.append("HOLDINGS:\n" + "\n".join(rows))
    except Exception as e:
        parts.append(f"PORTFOLIO: unavailable ({e}).")
    # Latest DR-Quant run
    try:
        import glob
        import json as _j
        files = sorted(glob.glob(str(_quant_dir() / "*.json")), key=os.path.getmtime)
        if files:
            res = _j.loads(Path(files[-1]).read_text())
            val = res.get("validated", [])
            parts.append(f"DR-QUANT (latest): {res.get('candidates')} candidates, "
                         f"{len(val)} validated. Top: "
                         + ", ".join(str(v.get('symbol') or v.get('ticker')) for v in val[:10]))
    except Exception:
        pass
    # Benchmark + calendar — so the assistant can answer "am I beating the
    # market?" and "what's coming up?" without the user leaving the chat.
    try:
        from src.llm.insights import benchmark_context, calendar_context
        perf = _PERF_CACHE.get("data")
        if perf and (perf.get("benchmark") or {}).get("stats"):
            parts.append(benchmark_context(perf))
        if _CAL_CACHE.get("data"):
            parts.append(calendar_context(_CAL_CACHE["data"], limit=10))
    except Exception:
        pass
    # Universe map / KB
    try:
        from src.kb import KnowledgeBase
        kb = KnowledgeBase.get()
        st = kb.stats()
        parts.append(f"UNIVERSE/KB: {st.get('universe_stocks', 0)} stocks, "
                     f"{st.get('documents', 0)} docs, {st.get('chunks', 0)} chunks.")
        buys = [s for s in (kb.all_stocks() or [])
                if s.get("recommendation") in ("STRONG_BUY", "BUY")][:12]
        if buys:
            parts.append("KB BUY candidates: "
                         + ", ".join(f"{b.get('symbol')}({b.get('recommendation')})" for b in buys))
    except Exception:
        pass
    return "\n\n".join(parts) if parts else "No data loaded yet."


@app.post("/api/chat")
def api_chat(body: dict):
    """Free-form chat grounded in the user's loaded data (portfolio, DR-Quant,
    Universe Map). Synchronous — returns the answer inline."""
    msg = (body.get("message") or body.get("question") or "").strip()
    if not msg:
        return {"ok": False, "error": "empty message"}
    try:
        from src.llm import get_llm
        context = _chat_context()
        system = (
            "You are the in-app portfolio assistant for an Indian-equities quant "
            "app. Answer concisely and specifically using ONLY the user's data "
            "below; if something isn't present, say so. Data:\n\n" + context)
        reply = (get_llm().complete(system, msg) or "").strip()
        if not reply or reply.lstrip().startswith("["):
            return {"ok": False, "error": reply or "LLM returned nothing"}
        return {"ok": True, "reply": reply}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ---------------- telegram ----------------
@app.post("/api/telegram/send-report")
def api_tg_report():
    from src.portfolio import PortfolioManager, ReportBuilder
    from src.telegram.bot import TelegramBot
    snap = PortfolioManager().snapshot()
    paths = ReportBuilder().build(snap)
    bot = TelegramBot()
    caption = (
        f"Invested ₹{snap.summary['holdings_invested']:.0f} | "
        f"Value ₹{snap.summary['holdings_value']:.0f} | "
        f"P&L ₹{snap.summary['holdings_pnl']:.0f}"
    )
    bot.broadcast_document(paths["xlsx"], caption=caption)
    return {"ok": True, "xlsx": paths["xlsx"]}


# ---------------- reports download ----------------
@app.get("/api/reports/latest")
def api_latest_report():
    d = settings.cache_dir / "reports"
    if not d.exists():
        raise HTTPException(404, "No reports yet.")
    files = sorted(d.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise HTTPException(404, "No xlsx reports yet.")
    return FileResponse(files[0], filename=files[0].name)


# ---------------- diagnostic: screener.in live fetch ----------------
@app.get("/api/debug/screener")
def api_debug_screener(symbol: str = "RELIANCE"):
    """Live-fetch a stock's page from screener.in, save the HTML to disk,
    and return diagnostics + parsed result. Use to verify the scraper end-to-end.
    GET /api/debug/screener?symbol=RELIANCE
    """
    from src.tools.screener_in import debug_fetch
    try:
        return debug_fetch(symbol)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ---------------- Knowledge Base ----------------
def _kb_uploads_dir() -> Path:
    d = settings.cache_dir / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


@app.get("/api/kb/stats")
def api_kb_stats():
    try:
        from src.kb import KnowledgeBase
        return {"ok": True, **KnowledgeBase.get().stats()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/kb/documents")
def api_kb_docs():
    try:
        from src.kb import KnowledgeBase
        return {"ok": True, "documents": KnowledgeBase.get().documents()}
    except Exception as e:
        return {"ok": False, "error": str(e), "documents": []}


@app.post("/api/kb/upload")
async def api_kb_upload(file: UploadFile = File(...),
                        title: str = Form("")):
    """Accept a file, save it under .cache/uploads/, ingest into the KB."""
    from src.kb import ingest_file
    dest = _kb_uploads_dir() / file.filename
    content = await file.read()
    dest.write_bytes(content)
    res = ingest_file(dest, title=title or None)
    return res


@app.post("/api/kb/ingest-text")
def api_kb_ingest_text(body: dict):
    from src.kb import ingest_text
    return ingest_text(
        title=body.get("title") or "untitled note",
        text=body.get("text") or "",
        source="manual",
    )


class _KBSearchBody(BaseModel):
    query: str
    k: int = 5


@app.post("/api/kb/search")
def api_kb_search(body: _KBSearchBody):
    from src.kb import KnowledgeBase
    return {"results": KnowledgeBase.get().search(body.query, k=body.k)}


@app.delete("/api/kb/document/{doc_id}")
def api_kb_delete(doc_id: str):
    from src.kb import KnowledgeBase
    n = KnowledgeBase.get().delete_doc(doc_id)
    return {"ok": True, "chunks_removed": n}


@app.post("/api/kb/export-finetune")
def api_kb_export():
    """Dump indexed KB chunks AND captured agent decisions into MLX-LM JSONL."""
    from src.kb.ingest import export_for_finetuning
    from src.kb import KnowledgeBase
    docs_out = settings.cache_dir / "finetune_corpus_docs.jsonl"
    dec_out = settings.cache_dir / "finetune_corpus_decisions.jsonl"
    export_for_finetuning(docs_out)
    n_dec = KnowledgeBase.get().export_decisions(dec_out)
    return {
        "ok": True,
        "docs_path": str(docs_out),
        "decisions_path": str(dec_out),
        "decisions": n_dec,
        "docs_size_kb": docs_out.stat().st_size // 1024 if docs_out.exists() else 0,
        "decisions_size_kb": dec_out.stat().st_size // 1024 if dec_out.exists() else 0,
    }


@app.post("/api/kb/search-decisions")
def api_kb_search_decisions(body: dict):
    from src.kb import KnowledgeBase
    return {"results": KnowledgeBase.get().search_decisions(
        body.get("query", ""), k=body.get("k", 5))}


# ---------------- Upstox auth (in-browser) ----------------
@app.post("/api/upstox/config")
def api_upstox_config(body: dict):
    """Push Upstox OAuth credentials into the running process (no restart).
    Lets the Android native login set Client ID / Secret / Redirect URI live
    before generating the auth URL."""
    for env_key, body_key in (("UPSTOX_API_KEY", "api_key"),
                              ("UPSTOX_API_SECRET", "api_secret"),
                              ("UPSTOX_REDIRECT_URI", "redirect_uri")):
        # Strip surrounding quotes/whitespace — a common paste error (copying
        # `"60f..."` straight from .env) is the usual cause of UDAPI100068.
        v = (body.get(body_key) or "").strip().strip('"').strip("'").strip()
        if v:
            os.environ[env_key] = v
    # Starting an OAuth login → a stale direct bearer token (Priority 1 in
    # load_token) must not shadow the OAuth token we're about to get.
    os.environ.pop("UPSTOX_BEARER_TOKEN", None)
    try:
        settings.refresh()
    except Exception:
        pass
    return {"ok": True,
            "have_key": bool(settings.upstox_api_key),
            "have_secret": bool(settings.upstox_api_secret),
            "redirect_uri": settings.upstox_redirect_uri}


@app.get("/api/upstox/auth-url")
def api_upstox_auth_url():
    """Return the Upstox OAuth login URL to open in a new tab."""
    from src.upstox.auth import build_auth_url

    # Reload settings before generating URL to catch any new changes
    settings.refresh()

    key = settings.upstox_api_key
    uri = settings.upstox_redirect_uri

    print(f"[API] Auth Request. Key={repr(key)} URI={repr(uri)}")

    secret = settings.upstox_api_secret
    if not key or not secret:
        missing = []
        if not key:
            missing.append("Client ID")
        if not secret:
            missing.append("Client Secret")
        return JSONResponse(
            {"ok": False,
             "error": f"{' and '.join(missing)} not set on the backend. "
                      f"Fill it in the login dialog and tap Get login link again."},
            status_code=200,
        )
    try:
        url = build_auth_url()
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=200)
    return {"ok": True, "url": url, "redirect_uri": uri}


class _UpstoxCodeBody(BaseModel):
    code_or_url: str


@app.post("/api/upstox/test-token")
def api_upstox_test_token():
    """Immediately test the current Upstox token (Bearer or OAuth)."""
    from src.upstox.client import UpstoxClient, UpstoxAuthError

    # Force reload settings from environment
    settings.refresh()

    try:
        client = UpstoxClient()
        prof = client.profile()
        name = prof.get("user_name") or prof.get("email") or "(Success)"
        return {"ok": True, "message": f"Authenticated successfully as: {name}"}
    except UpstoxAuthError as e:
        return {"ok": False, "message": str(e)}
    except Exception as e:
        return {"ok": False, "message": f"Connection Error: {str(e)}"}


@app.post("/api/upstox/exchange-code")
def api_upstox_exchange(body: _UpstoxCodeBody):
    """Exchange an Upstox OAuth code (or the full redirected URL) for a token.
    Called by the in-WebView Android login and the Mac paste flow."""
    import re
    import urllib.parse
    from src.upstox.auth import exchange_and_save

    text = (body.code_or_url or "").strip().strip("`'\"")
    code = None
    try:
        q = urllib.parse.urlparse(text).query
        if q:
            params = urllib.parse.parse_qs(q)
            if "code" in params:
                code = params["code"][0]
    except Exception:
        pass
    if not code:
        m = re.search(r"code[=:\s]+([A-Za-z0-9_\-\.]+)", text)
        if m:
            code = m.group(1)
    if not code and re.fullmatch(r"[A-Za-z0-9_\-\.]{8,}", text):
        code = text
    if not code:
        return {"ok": False, "error": "Could not find a `code` value. Paste the full redirected URL."}

    try:
        exchange_and_save(code)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    # Token saved → success. Profile is best-effort (don't fail a good login).
    name = "you"
    try:
        from src.upstox.client import UpstoxClient
        prof = UpstoxClient().profile()
        name = prof.get("user_name") or prof.get("email") or "(unknown)"
    except Exception as e:
        print(f"[Upstox exchange] token saved; profile check deferred: {e}")
    return {"ok": True, "user": name}


@app.get("/callback", response_class=HTMLResponse)
def upstox_callback(code: str = "", error: str = "", error_description: str = ""):
    """Upstox OAuth redirect target. Because the backend runs on the phone at
    the SAME host as the registered redirect_uri (localhost:8000/callback), the
    browser lands here directly after login and we exchange the code instantly —
    no copy-paste, and the single-use code is consumed immediately while fresh."""
    def _page(title: str, detail: str, ok: bool) -> HTMLResponse:
        color = "#3FB950" if ok else "#F85149"
        return HTMLResponse(
            f"""<!doctype html><html><head><meta name="viewport"
            content="width=device-width,initial-scale=1"><title>Upstox login</title></head>
            <body style="margin:0;background:#0B0E14;color:#E6EDF3;font-family:-apple-system,sans-serif;
            display:flex;min-height:100vh;align-items:center;justify-content:center;text-align:center">
            <div style="padding:24px"><div style="font-size:48px">{'✅' if ok else '❌'}</div>
            <h2 style="color:{color}">{title}</h2>
            <p style="color:#8B949E;font-size:14px;max-width:320px">{detail}</p>
            <p style="color:#8B949E;font-size:13px">You can close this tab and return to the app.</p>
            </div></body></html>"""
        )

    if error or error_description:
        return _page("Login failed", error_description or error, ok=False)
    if not code:
        return _page("No auth code", "Upstox didn't return a code in the redirect.", ok=False)
    try:
        from src.upstox.auth import exchange_and_save
        exchange_and_save(code)   # exchange + persist; raises only on real failure
    except Exception as e:
        return _page("Token exchange failed", str(e), ok=False)

    # Token is saved → login succeeded. The profile lookup is best-effort only;
    # a transient hiccup here must NOT report the login as failed.
    name = "you"
    try:
        from src.upstox.client import UpstoxClient
        prof = UpstoxClient().profile()
        name = prof.get("user_name") or prof.get("email") or "you"
    except Exception as e:
        print(f"[Upstox callback] token saved; profile check deferred: {e}")
    return _page(f"Logged in as {name}", "Your Upstox token is saved on this device.", ok=True)


# ---------------- broker selection (either/or) ----------------
@app.get("/api/broker")
def api_broker_get():
    from src.brokers import broker_status
    return broker_status()


@app.post("/api/broker")
def api_broker_set(body: dict):
    """Switch the active broker (either/or). Persists for this process; also
    write it to .cache so it survives a restart on this host."""
    import os
    b = (body.get("broker") or "").lower()
    if b not in ("upstox", "groww"):
        return {"ok": False, "error": "broker must be 'upstox' or 'groww'"}
    os.environ["BROKER"] = b
    try:
        settings.refresh()
    except Exception:
        pass
    try:
        (settings.cache_dir / "active_broker.txt").write_text(b)
    except Exception:
        pass
    from src.brokers import broker_status
    return {"ok": True, **broker_status()}


@app.post("/api/broker/test")
def api_broker_test():
    """Broker-aware auth check (works for whichever broker is active).
    Returns {ok, message} so the Android direct-token flow can verify any broker."""
    settings.refresh()
    from src.brokers import broker_status
    s = broker_status()
    if s.get("ok"):
        return {"ok": True, "message": f"Authenticated as {s.get('user')} ({s.get('active')})"}
    return {"ok": False, "message": s.get("error") or "authentication failed"}


@app.post("/api/groww/login")
def api_groww_login(body: dict):
    """Groww TOTP/secret login — the robust equivalent of Upstox OAuth.
    Pushes creds live, mints a daily access token, persists it, switches the
    active broker to groww, and verifies. Returns {ok, message}."""
    import os

    def _clean(v):
        return (str(v or "")).strip().strip('"').strip("'").strip()

    api_key = _clean(body.get("api_key"))
    totp_secret = _clean(body.get("totp_secret")).replace(" ", "")
    secret = _clean(body.get("secret") or body.get("api_secret"))

    if api_key:
        os.environ["GROWW_API_KEY"] = api_key
    if totp_secret:
        os.environ["GROWW_TOTP_SECRET"] = totp_secret
    if secret:
        os.environ["GROWW_API_SECRET"] = secret
    # A stale direct token would shadow the freshly-minted one.
    os.environ.pop("GROWW_ACCESS_TOKEN", None)

    from src.brokers.groww_auth import login as groww_login
    try:
        groww_login(api_key or None, totp_secret or None, secret or None)
    except Exception as e:
        return {"ok": False, "message": f"{e}"}

    os.environ["BROKER"] = "groww"
    try:
        settings.refresh()
        (settings.cache_dir / "active_broker.txt").write_text("groww")
    except Exception:
        pass
    from src.brokers import broker_status
    s = broker_status()
    if s.get("ok"):
        return {"ok": True, "message": f"Logged in to Groww as {s.get('user')}"}
    # Token minted but verification call failed — still treat as logged in
    # (best-effort, same as Upstox), surfacing the detail.
    return {"ok": True, "message": "Groww token minted and saved.",
            "warning": s.get("error")}


@app.post("/api/groww/save-token")
def api_groww_save_token(body: dict):
    """Save a Groww daily access token (the 'direct bearer' equivalent)."""
    from src.brokers.groww_auth import save_token
    tok = body.get("token") or body.get("access_token") or ""
    if not tok.strip():
        return {"ok": False, "error": "empty token"}
    import os
    save_token(tok)
    os.environ["BROKER"] = "groww"
    try:
        settings.refresh()
        (settings.cache_dir / "active_broker.txt").write_text("groww")
    except Exception:
        pass
    from src.brokers import broker_status
    return {"ok": True, **broker_status()}


# ---------------- settings / status ----------------
_SCHED = {"obj": None}


@app.get("/api/status")
def api_status():
    """Health-check every subsystem. The UI uses this to render the dashboard."""
    out = {"build": _build_info()}

    # Active broker (either/or)
    try:
        from src.brokers import broker_status
        out["broker"] = broker_status()
    except Exception as e:
        out["broker"] = {"active": settings.broker, "ok": False, "error": str(e)[:200]}

    # Upstox (kept for the Upstox-specific login card)
    try:
        from src.upstox.client import UpstoxClient
        prof = UpstoxClient().profile()
        out["upstox"] = {"ok": True, "user": prof.get("user_name") or prof.get("email", "?")}
    except Exception as e:
        out["upstox"] = {"ok": False, "error": str(e)[:200]}

    # LLM provider
    p = settings.llm_provider
    if p == "ollama":
        try:
            import requests
            r = requests.get(f"{settings.ollama_host}/api/tags", timeout=3).json()
            models = [m["name"] for m in r.get("models", [])]
            out["llm"] = {"ok": settings.ollama_model in models,
                          "provider": "ollama", "model": settings.ollama_model,
                          "models_available": models[:8]}
        except Exception as e:
            out["llm"] = {"ok": False, "provider": "ollama", "error": str(e)[:200]}
    else:
        out["llm"] = {"ok": bool(settings.anthropic_api_key),
                      "provider": "anthropic", "model": settings.anthropic_model}

    # Telegram
    out["telegram"] = {"configured": bool(settings.telegram_bot_token),
                       "authorized_chats": list(settings.telegram_allowed_chat_ids)}

    # DB
    try:
        from src.db import get_db
        out["db"] = {"timescale": get_db().live,
                     "dsn_set": bool(settings.timescale_dsn)}
    except Exception as e:
        out["db"] = {"timescale": False, "error": str(e)[:200]}

    # Scheduler
    out["scheduler"] = {"available": _scheduler_available(),
                        "running": bool(_SCHED["obj"] and _SCHED["obj"].running),
                        "jobs": [
                            {"id": j.id, "name": j.name, "next": str(j.next_run_time)}
                            for j in (_SCHED["obj"].get_jobs() if _SCHED["obj"] else [])
                        ]}

    out["config"] = {
        "llm_provider": settings.llm_provider,
        "ollama_host": settings.ollama_host,
        "upstox_api_key": f"{settings.upstox_api_key[:4]}***" if settings.upstox_api_key else "MISSING",
        "upstox_api_secret": "SET" if settings.upstox_api_secret else "MISSING",
        "upstox_bearer_token": "PROVIDED" if os.getenv("UPSTOX_BEARER_TOKEN") else "OAUTH_FLOW",
        "upstox_redirect_uri": settings.upstox_redirect_uri,
        "telegram_token": f"{settings.telegram_bot_token[:4]}***" if settings.telegram_bot_token else "MISSING",
        "cache_dir": str(settings.cache_dir),
        "risk_free_rate": settings.risk_free_rate,
    }
    return out


def _scheduler_available() -> bool:
    try:
        from src.scheduler import HAS_APSCHEDULER
        return bool(HAS_APSCHEDULER)
    except Exception:
        return False


@app.post("/api/scheduler/start")
def api_sched_start():
    if not _scheduler_available():
        return {"ok": False, "error": "Automatic scheduling isn't available in "
                                      "this build — APScheduler ships on desktop, "
                                      "not in the app. You can still trigger each "
                                      "job manually."}
    from src.scheduler import build_scheduler
    if _SCHED["obj"] and _SCHED["obj"].running:
        return {"ok": True, "already_running": True}
    sched = build_scheduler(background=True)
    sched.start()
    _SCHED["obj"] = sched
    return {"ok": True, "jobs": [j.id for j in sched.get_jobs()]}


@app.post("/api/scheduler/stop")
def api_sched_stop():
    if _SCHED["obj"]:
        _SCHED["obj"].shutdown(wait=False)
        _SCHED["obj"] = None
    return {"ok": True}


@app.post("/api/scheduler/run-now/{job_name}")
def api_sched_run_now(job_name: str):
    """Manually trigger one of the scheduler jobs without waiting for cron."""
    from src.scheduler.jobs import (
        job_eod_report, job_full_funnel, job_intraday, job_macro_check,
    )
    JOBS = {"macro": job_macro_check, "funnel": job_full_funnel,
            "intraday": job_intraday, "eod": job_eod_report}
    fn = JOBS.get(job_name)
    if not fn:
        raise HTTPException(404, f"unknown job '{job_name}'")
    job_id = uuid.uuid4().hex[:8]
    _run_job(job_id, fn)
    return {"job_id": job_id}


_TG_BOT_PROC = {"obj": None}      # subprocess.Popen — separate process, matches Upstox_Agent pattern


def _bot_log_path():
    p = settings.cache_dir / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p / "telegram_bot.log"


@app.post("/api/telegram/bot/start")
def api_tg_bot_start():
    """Spawn the Telegram bot as its OWN process so PTB owns the main thread.

    Running PTB's run_polling() inside a uvicorn worker thread causes httpx
    ReadTimeouts on getMe — Upstox_Agent avoids this by running the bot as
    its own process. We do the same.
    """
    import subprocess
    import sys

    # Android (Chaquopy) has no runnable Python binary to spawn — run the bot
    # IN-PROCESS on a background thread instead of a subprocess.
    if os.getenv("APP_FILES_DIR"):
        try:
            from src.telegram.bot import CommandBot
            from src.telegram.handlers import HANDLERS
            import threading
            if _TG_BOT_PROC.get("thread") and _TG_BOT_PROC["thread"].is_alive():
                return {"ok": True, "already_running": True, "in_process": True}
            t = threading.Thread(
                target=lambda: CommandBot(HANDLERS).run(), daemon=True)
            t.start()
            _TG_BOT_PROC["thread"] = t
            return {"ok": True, "in_process": True}
        except Exception as e:
            return {"ok": False, "error": f"telegram bot failed: {e}"}

    proc = _TG_BOT_PROC["obj"]
    if proc and proc.poll() is None:
        return {"ok": True, "already_running": True, "pid": proc.pid}

    try:
        log_fh = _bot_log_path().open("a")
        log_fh.write(f"\n=== bot started at {datetime.now().isoformat()} ===\n")
        log_fh.flush()
        # Use the same Python that's running the dashboard
        proc = subprocess.Popen(
            [sys.executable, "main.py", "telegram", "bot"],
            cwd=str(Path(__file__).resolve().parents[2]),
            stdout=log_fh, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _TG_BOT_PROC["obj"] = proc
        return {"ok": True, "started": True, "pid": proc.pid,
                "log": str(_bot_log_path())}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/telegram/bot/stop")
def api_tg_bot_stop():
    import signal
    proc = _TG_BOT_PROC["obj"]
    if not proc or proc.poll() is not None:
        return {"ok": True, "already_stopped": True}
    try:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        return {"ok": True}
    finally:
        _TG_BOT_PROC["obj"] = None


@app.get("/api/telegram/bot/status")
def api_tg_bot_status():
    proc = _TG_BOT_PROC["obj"]
    return {
        "running": bool(proc and proc.poll() is None),
        "pid": proc.pid if proc else None,
        "log": str(_bot_log_path()),
    }


@app.get("/api/telegram/bot/log")
def api_tg_bot_log(tail: int = 80):
    p = _bot_log_path()
    if not p.exists():
        return {"lines": []}
    lines = p.read_text().splitlines()[-tail:]
    return {"lines": lines}


@app.post("/api/telegram/test")
def api_tg_test():
    """End-to-end smoke test: getMe (auth) → broadcast (delivery)."""
    from src.telegram.bot import TelegramBot, _load_authed
    try:
        bot = TelegramBot()
        me = bot.get_me()
        if not me.get("ok"):
            return {"ok": False, "stage": "getMe",
                    "error": me.get("description", "unknown")}
        chats = list(_load_authed())
        if not chats:
            return {"ok": False, "stage": "config",
                    "error": "No authorized chat_ids. Set CHAT_ID in .env."}
        sends = []
        for cid in chats:
            r = bot.send_message(cid, "✅ Test from Portfolio dashboard.")
            sends.append({"chat_id": cid, "ok": r.get("ok"),
                          "error": r.get("description")})
        return {"ok": all(s["ok"] for s in sends),
                "bot": me.get("result", {}).get("username"),
                "sends": sends}
    except Exception as e:
        return {"ok": False, "stage": "exception", "error": str(e)}


# ---------------- Universe Map ----------------
_UMAP_JOBS: dict[str, dict] = {}
# job_id -> latest progress dict from the builder's callback. Lets the UI show
# a real bar during a crawl that can legitimately run for an hour.
_UMAP_PROGRESS: dict[str, dict] = {}


@app.get("/api/universe-map/progress/{job_id}")
def api_umap_progress(job_id: str):
    """Live progress for a running build (stage / done / total / pct)."""
    prog = _UMAP_PROGRESS.get(job_id)
    job = JOBS.get(job_id)
    status = (job or {}).get("status")
    info = _UMAP_JOBS.get(job_id)
    if status is None and info:
        proc = info.get("proc")
        status = "running" if (proc and proc.poll() is None) else "done"
        # The desktop path runs the crawl in a subprocess, so there's no
        # in-process callback — recover progress from its log instead, which
        # already prints "[UMAP] 250/1900 processed (...)".
        if not prog:
            prog = _progress_from_log(info.get("log"))
    return {"ok": True, "job_id": job_id, "status": status or "unknown",
            "progress": prog or {}}


def _progress_from_log(log_path) -> dict:
    """Parse the newest '[UMAP] done/total processed' line out of a build log."""
    if not log_path:
        return {}
    try:
        import re
        path = Path(log_path)
        if not path.exists():
            return {}
        # Only the tail matters; these logs get long.
        with path.open("rb") as fh:
            fh.seek(max(0, path.stat().st_size - 20000))
            tail = fh.read().decode("utf-8", errors="replace")
        hits = re.findall(
            r"\[UMAP\] (\d+)/(\d+) processed \((\d+) fetched, (\d+) reused",
            tail)
        if hits:
            done, total, fetched, reused = (int(x) for x in hits[-1])
            return {"stage": "fundamentals", "done": done, "total": total,
                    "fetched": fetched, "reused": reused,
                    "pct": round(done / total * 100, 1) if total else 0.0,
                    "message": f"{done} of {total} stocks"}
        if "Stage A" in tail:
            return {"stage": "technical", "done": 0, "total": 0, "pct": 0.0,
                    "message": "Scanning the universe for price data…"}
    except Exception as e:
        log = get_logger("dashboard")
        log.debug(f"umap log progress parse failed: {e}")
    return {}


@app.get("/api/universe-map/report")
def api_umap_report(universe: str = "all_nse", fmt: str = "xlsx"):
    """Download everything collected for a universe as CSV or multi-sheet Excel."""
    import pandas as pd
    from src.universe_map import load_cached

    data = load_cached(universe)
    if not data or not data.get("stocks"):
        raise HTTPException(404, f"No universe map data for '{universe}'. Build it first.")

    df = pd.DataFrame(data["stocks"])
    # Order the most useful columns first
    preferred = ["symbol", "name", "sector", "industry", "ltp",
                 "tech_score", "fund_score", "combined", "recommendation",
                 "PE", "ROE", "DE", "sales_growth_pct", "profit_growth_pct",
                 "market_cap_cr", "rsi", "ret_1m_pct", "ret_3m_pct",
                 "near_52w_high_pct", "atr_pct", "setups", "fund_sources"]
    cols = [c for c in preferred if c in df.columns] + \
           [c for c in df.columns if c not in preferred]
    df = df[cols]
    if "combined" in df.columns:
        df = df.sort_values("combined", ascending=False, na_position="last")

    out_dir = settings.cache_dir / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    built = data.get("built_at", "")

    if fmt == "csv":
        path = out_dir / f"universe_{universe}_{ts}.csv"
        df.to_csv(path, index=False)
        return FileResponse(path, filename=path.name, media_type="text/csv")

    # Excel — multi-sheet: All, plus a sheet per recommendation bucket
    path = out_dir / f"universe_{universe}_{ts}.xlsx"
    with pd.ExcelWriter(path, engine="xlsxwriter") as xw:
        meta_df = pd.DataFrame([
            ("universe", universe), ("built_at", built),
            ("total_stocks", len(df)),
            ("technically_scored", data.get("tech_total")),
            ("fundamentals_fetched", data.get("fund_scanned")),
            ("fundamentals_reused_from_KB", data.get("fund_reused")),
        ], columns=["field", "value"])
        meta_df.to_excel(xw, sheet_name="Summary", index=False)
        df.to_excel(xw, sheet_name="All Stocks", index=False)
        if "recommendation" in df.columns:
            for reco in ["STRONG_BUY", "BUY", "TECH_BUY", "HOLD",
                         "TECH_WATCH", "AVOID", "SELL"]:
                sub = df[df["recommendation"] == reco]
                if not sub.empty:
                    sub.to_excel(xw, sheet_name=reco[:31], index=False)
        if "sector" in df.columns:
            sector_summary = (df.groupby("sector")
                              .agg(stocks=("symbol", "count"),
                                   avg_tech=("tech_score", "mean"),
                                   avg_fund=("fund_score", "mean"),
                                   avg_combined=("combined", "mean"))
                              .round(1).reset_index()
                              .sort_values("avg_combined", ascending=False,
                                           na_position="last"))
            sector_summary.to_excel(xw, sheet_name="By Sector", index=False)
    return FileResponse(
        path, filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/api/universe-map/reset")
def api_umap_reset():
    """Wipe instrument cache, blacklist, daily candles, screener.in cache,
    and universe-map cache. Use this when the filter changes or you suspect
    stale data has polluted the universe."""
    import shutil
    cleared = []
    paths_to_clear = [
        settings.cache_dir / "instruments_NSE.csv",
        settings.cache_dir / "instruments_BSE.csv",
        settings.cache_dir / "instruments_NSE.parquet",  # legacy
        settings.cache_dir / "instruments_BSE.parquet",  # legacy
        settings.cache_dir / "instrument_blacklist.json",
        settings.cache_dir / "daily",
        settings.cache_dir / "screener_in",
        settings.cache_dir / "fundamentals",
        settings.cache_dir / "universe_map",
    ]
    for p in paths_to_clear:
        try:
            if not p.exists():
                continue
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            cleared.append(p.name)
        except Exception as e:
            log = get_logger("dashboard")
            log.warning(f"reset failed for {p}: {e}")
    # Re-create empty dirs we expect
    (settings.cache_dir / "universe_map").mkdir(parents=True, exist_ok=True)
    return {"ok": True, "cleared": cleared}


def _scrub_for_json(v):
    """Make a payload safe for Starlette's strict JSON serializer.

    Two distinct hazards, both of which have 500'd this API before:

      * NaN / ±Inf floats, which the encoder rejects outright;
      * numpy scalar types. Anything that has been near pandas or numpy leaks
        np.float64 / np.int64 / np.bool_ into plain dicts, and FastAPI's
        jsonable_encoder has no rule for them — it falls back to dict(obj) then
        vars(obj) and raises "'numpy.bool_' object is not iterable". np.float64
        happens to subclass float so it slips through; np.bool_ and np.int64 do
        not, and those are the ones that bite.
    """
    import math

    import numpy as np

    if v is None:
        return None
    if isinstance(v, np.generic):          # any numpy scalar → Python native
        v = v.item()
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, dict):
        return {k: _scrub_for_json(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_scrub_for_json(x) for x in v]
    if isinstance(v, np.ndarray):
        return [_scrub_for_json(x) for x in v.tolist()]
    return v


@app.get("/api/universe-map/data")
def api_umap_data(universe: str = "all_nse"):
    from src.universe_map import load_cached
    data = load_cached(universe)
    if not data:
        return {"ok": False, "error": f"No cached map for '{universe}'. Build one first."}
    return {"ok": True, **_scrub_for_json(data)}


@app.post("/api/universe-map/build")
def api_umap_build(body: dict):
    universe = body.get("universe", "all_nse")
    max_age_days = float(body.get("max_age_days", 7.0))
    job_id = uuid.uuid4().hex[:8]

    # Android cannot easily use subprocess.Popen with sys.executable.
    # We switch to a ThreadPool job for Android.
    import os
    if os.getenv("APP_FILES_DIR"):
        def _build_in_process():
            from src.universe_map.builder import build_universe_map
            # Note: workers reduced to 1 for stability in-process on mobile
            return build_universe_map(
                universe=universe, max_age_days=max_age_days, workers=1,
                progress=lambda p: _UMAP_PROGRESS.__setitem__(job_id, p))

        # Bound the map — the on-device backend is long-lived and this would
        # otherwise grow one entry per build for the life of the process.
        while len(_UMAP_PROGRESS) > 20:
            _UMAP_PROGRESS.pop(next(iter(_UMAP_PROGRESS)), None)
        _UMAP_PROGRESS[job_id] = {"stage": "starting", "done": 0, "total": 0,
                                  "pct": 0.0, "message": "Starting…"}
        _run_job(job_id, _build_in_process)
        # Mock a status so the UI thinks it's a subproc job if it polls specific subproc endpoints
        # though pollJob uses /api/jobs/ which handles both.
        return {"ok": True, "job_id": job_id, "in_process": True}

    import subprocess
    import sys as _sys
    log_path = (settings.cache_dir / "universe_map" / f"{job_id}.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = log_path.open("w")
    log_fh.write(f"=== umap job {job_id} · universe={universe} · "
                 f"max_age_days={max_age_days} · "
                 f"{datetime.now().isoformat(timespec='seconds')} ===\n")
    log_fh.flush()

    proj_root = Path(__file__).resolve().parents[2]
    proc = subprocess.Popen(
        [_sys.executable, "-u", "main.py", "quant", "universe-map",
         "--universe", universe, "--max-age-days", str(max_age_days)],
        cwd=str(proj_root), stdout=log_fh, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _UMAP_JOBS[job_id] = {"proc": proc, "log": log_path, "universe": universe}
    return {"ok": True, "job_id": job_id, "pid": proc.pid}


@app.get("/api/universe-map/job/{job_id}")
def api_umap_job(job_id: str):
    info = _UMAP_JOBS.get(job_id)
    if not info:
        # Check in-process jobs (Android fallback)
        if job_id in JOBS:
            job = JOBS[job_id]
            # Map in-process fields to what the UI expects for a "job"
            return {
                "status": job["status"],
                "exit_code": 0 if job["status"] == "done" else (1 if job["status"] == "error" else None),
                "pid": os.getpid(),
                "error": job.get("error"),
                "in_process": True
            }
        # IMPORTANT: return a TERMINAL status (HTTP 200), not 404.
        return {"status": "error", "exit_code": None, "pid": None,
                "error": "job not found (dashboard restarted or job expired)"}
    rc = info["proc"].poll()
    status = "running" if rc is None else ("done" if rc == 0 else "error")
    return {"status": status, "exit_code": rc, "pid": info["proc"].pid,
            "log": str(info["log"])}


@app.get("/api/universe-map/log/{job_id}")
def api_umap_log(job_id: str, since: int = 0):
    info = _UMAP_JOBS.get(job_id)
    if not info:
        if job_id in JOBS:
            msg = "[Android] Build running in-process. See System Terminal for live logs."
            if JOBS[job_id]["status"] == "error":
                msg = f"[Android] ERROR: {JOBS[job_id].get('error')}. See Terminal for details."
            return {"lines": [{"msg": msg}], "next": since + 1}
        return {"lines": [], "next": since}
    if not info["log"].exists():
        return {"lines": [], "next": since}
    size = info["log"].stat().st_size
    if since >= size:
        return {"lines": [], "next": size}
    with info["log"].open("rb") as fh:
        fh.seek(since)
        chunk = fh.read().decode("utf-8", errors="replace")
    lines = [{"ts": "", "level": "INFO", "logger": "umap", "msg": l}
             for l in chunk.splitlines() if l.strip()]
    return {"lines": lines, "next": size}


# ---------------- D-R1-Quant ----------------
@app.get("/api/quant/macro")
def api_quant_macro():
    from src.tools import MacroSnapshot
    return MacroSnapshot().market_mode()


# Quant runs now use SUBPROCESS isolation (same pattern as the Telegram bot).
# Why: ThreadPoolExecutor + heavy imports (chromadb / yfinance / torch) kept
# silently deadlocking inside the worker thread on macOS. A subprocess has
# full process isolation, its own asyncio + import locks, and a log file we
# can tail.
_QUANT_JOBS: dict[str, dict] = {}


def _tprint(*args):
    import sys
    print("[QUANT]", *args, flush=True, file=sys.stderr)


def _quant_dir():
    d = settings.cache_dir / "quant_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


@app.post("/api/quant/run")
def api_quant_run(body: dict):
    job_id = uuid.uuid4().hex[:8]
    universe = body.get("universe", "nifty50")
    _tprint(f"POST /api/quant/run → job={job_id} universe={universe}")

    # Android cannot easily use subprocess.Popen with sys.executable.
    # We switch to a ThreadPool job for Android.
    import os
    if os.getenv("APP_FILES_DIR"):
        def _quant_in_process():
            # Import and run the quant engine directly
            from src.scheduler.jobs import job_full_funnel_sync
            try:
                res = job_full_funnel_sync(universe=universe)
            except ImportError:
                # If specialized sync job doesn't exist, use common one
                from src.scheduler.jobs import job_full_funnel
                res = job_full_funnel()
            return _capture_quant(res)

        _run_job(job_id, _quant_in_process)
        return {"job_id": job_id, "in_process": True}

    import subprocess
    import sys

    log_path = _quant_dir() / f"{job_id}.log"
    result_path = _quant_dir() / f"{job_id}.json"
    log_fh = log_path.open("w")
    log_fh.write(f"=== job {job_id} · universe={universe} · "
                 f"{datetime.now().isoformat(timespec='seconds')} ===\n")
    log_fh.flush()

    proj_root = Path(__file__).resolve().parents[2]
    try:
        proc = subprocess.Popen(
            [
                sys.executable, "-u", "main.py", "quant", "run",
                "--universe", universe,
                "--result-file", str(result_path),
            ],
            cwd=str(proj_root),
            stdout=log_fh, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception as e:
        log_fh.write(f"[DASHBOARD] failed to spawn subprocess: {e}\n")
        log_fh.close()
        return {"job_id": job_id, "ok": False, "error": str(e)}

    _QUANT_JOBS[job_id] = {
        "proc": proc, "log": log_path, "result": result_path,
        "universe": universe, "started": datetime.now().isoformat(),
    }
    _tprint(f"job={job_id} subprocess pid={proc.pid} log={log_path.name}")
    return {"job_id": job_id, "pid": proc.pid}


@app.get("/api/quant/log/{job_id}")
def api_quant_log(job_id: str, since: int = 0):
    """Tail the subprocess log file. `since` is a byte offset."""
    info = _QUANT_JOBS.get(job_id)
    if not info:
        # For Android in-process jobs, we don't have a file.
        if job_id in JOBS:
            msg = "[Android] Quant run in-process. See System Terminal for live logs."
            if JOBS[job_id]["status"] == "error":
                msg = f"[Android] ERROR: {JOBS[job_id].get('error')}. See Terminal for details."
            return {"lines": [{"ts": "", "level": "INFO", "logger": "android", "msg": msg}], "next": since + 1}
        return {"lines": [], "next": since}
    p = info["log"]
    if not p.exists():
        return {"lines": [], "next": since}
    size = p.stat().st_size
    if since >= size:
        return {"lines": [], "next": size}
    with p.open("rb") as fh:
        fh.seek(since)
        chunk = fh.read().decode("utf-8", errors="replace")
    lines = []
    for raw in chunk.splitlines():
        if not raw.strip():
            continue
        level = "INFO"
        low = raw.lower()
        if "fatal" in low or "error" in low or "traceback" in low:
            level = "ERROR"
        elif "warning" in low:
            level = "WARNING"
        # logger name guess: tokens like "screener.talib |" or "INFO d-r1-quant"
        logger_name = "subproc"
        for tok in raw.split():
            if "." in tok and "/" not in tok and len(tok) < 40:
                logger_name = tok.strip("|:"); break
        lines.append({"ts": "", "level": level, "logger": logger_name, "msg": raw})
    return {"lines": lines, "next": size}


@app.post("/api/quant/kill/{job_id}")
def api_quant_kill(job_id: str):
    import signal
    info = _QUANT_JOBS.get(job_id)
    if not info:
        return {"ok": False, "error": "unknown job"}
    if info["proc"].poll() is None:
        try:
            info["proc"].send_signal(signal.SIGTERM)
            info["proc"].wait(timeout=3)
        except Exception:
            info["proc"].kill()
    return {"ok": True}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    # Quant subprocess job?
    qj = _QUANT_JOBS.get(job_id)
    if qj:
        rc = qj["proc"].poll()
        if rc is None:
            return {"status": "running", "result": None, "error": None,
                    "pid": qj["proc"].pid}
        if rc == 0 and qj["result"].exists():
            import json as _j
            try:
                result = _capture_quant(_j.loads(qj["result"].read_text()))
                return {"status": "done", "result": result,
                        "error": None, "exit_code": rc}
            except Exception as e:
                return {"status": "error", "result": None,
                        "error": f"result file unparseable: {e}"}
        return {"status": "error", "result": None,
                "error": f"subprocess exited with code {rc}. "
                         f"See {qj['log'].name} in .cache/quant_runs/",
                "exit_code": rc}

    # Other threadpool jobs (optimizer, screener, etc.)
    j = JOBS.get(job_id)
    if not j:
        # Terminal status (HTTP 200), NOT 404 — same reasoning as
        # api_umap_job: a 404 makes polling streamers loop forever.
        return {"status": "error", "result": None,
                "error": "job not found (dashboard restarted or job expired)"}
    return j

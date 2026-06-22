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
    if isinstance(o, pd.DataFrame):
        return o.where(pd.notnull(o), None).to_dict("records")
    return o


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
    return {
        "summary": snap.summary,
        "holdings": _df(snap.holdings),
        "positions": _df(snap.positions),
        "allocation": _df(snap.allocation),
    }


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

    pm = PortfolioManager()
    snap = pm.snapshot()
    if snap.holdings.empty:
        return {"error": "no holdings to optimise around"}

    val_by_yf = {}
    for _, row in snap.holdings.iterrows():
        sym = row.get("tradingsymbol", "")
        yf = f"{sym}.NS"
        val_by_yf[yf] = float(row.get("current_value", 0))

    # Pull STRONG_BUY candidates from the KB universe store
    candidates: list[str] = []
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

    res = PortfolioOptimizer().deploy_cash(
        current_value_by_yf=val_by_yf,
        cash_to_deploy=cash,
        candidates_extra=candidates,
        max_weight=max_weight,
    )
    res["universe_candidates_considered"] = len(candidates)
    return res


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

        return {
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
        }

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
        return {
            "technical_count": len(tech),
            "final_count": len(full),
            "technical": _df(tech.head(100)),
            "results": _df(full),
        }

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

    if not (key and settings.upstox_api_secret):
        return JSONResponse(
            {"ok": False, "error": f"Credentials missing. Key={bool(key)} Secret={bool(settings.upstox_api_secret)}"},
            status_code=200,
        )
    return {"ok": True, "url": build_auth_url(), "redirect_uri": uri}


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
        from src.upstox.client import UpstoxClient
        prof = UpstoxClient().profile()
        name = prof.get("user_name") or prof.get("email") or "(unknown)"
        return {"ok": True, "user": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
        exchange_and_save(code)
        from src.upstox.client import UpstoxClient
        prof = UpstoxClient().profile()
        name = prof.get("user_name") or prof.get("email") or "you"
        return _page(f"Logged in as {name}", "Your Upstox token is saved on this device.", ok=True)
    except Exception as e:
        return _page("Token exchange failed", str(e), ok=False)


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
    out = {}

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
    out["scheduler"] = {"running": bool(_SCHED["obj"] and _SCHED["obj"].running),
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


@app.post("/api/scheduler/start")
def api_sched_start():
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
    """Strip NaN/±Inf so Starlette's strict JSON serializer doesn't 500."""
    import math
    if v is None:
        return None
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, dict):
        return {k: _scrub_for_json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_scrub_for_json(x) for x in v]
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
            return build_universe_map(universe=universe, max_age_days=max_age_days, workers=1)

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
                return job_full_funnel_sync(universe=universe)
            except ImportError:
                # If specialized sync job doesn't exist, use common one
                from src.scheduler.jobs import job_full_funnel
                return job_full_funnel()

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
                return {"status": "done",
                        "result": _j.loads(qj["result"].read_text()),
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

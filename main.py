"""Unified CLI.

D-R1-Quant funnel:
    python main.py quant macro
    python main.py quant scan-technical --universe nifty50
    python main.py quant run --universe nifty50
    python main.py quant schedule        # APScheduler daemon (IST trading hours)
    python main.py quant init-db         # apply TimescaleDB schema


Examples:
    python main.py auth login
    python main.py portfolio report
    python main.py portfolio risk --threshold 15
    python main.py screener scan --universe nifty50
    python main.py intraday analyze --days 60
    python main.py intraday scan
    python main.py agent portfolio "How is my portfolio doing and what should I trim?"
    python main.py agent screener  "Find me 3 strong buys from Nifty50."
    python main.py agent intraday  "Review my last 30 days and suggest setups today."
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich import print
from rich.table import Table

app = typer.Typer(add_completion=False, no_args_is_help=True)
auth_app = typer.Typer(no_args_is_help=True)
port_app = typer.Typer(no_args_is_help=True)
scr_app = typer.Typer(no_args_is_help=True)
intra_app = typer.Typer(no_args_is_help=True)
agent_app = typer.Typer(no_args_is_help=True)
tg_app = typer.Typer(no_args_is_help=True)
quant_app = typer.Typer(no_args_is_help=True)
broker_app = typer.Typer(no_args_is_help=True)

app.add_typer(auth_app, name="auth")
app.add_typer(port_app, name="portfolio")
app.add_typer(scr_app, name="screener")
app.add_typer(intra_app, name="intraday")
app.add_typer(agent_app, name="agent")
app.add_typer(tg_app, name="telegram")
app.add_typer(quant_app, name="quant")
app.add_typer(broker_app, name="broker")


# ---------- broker (either/or: upstox | groww) ----------
@broker_app.command("status")
def broker_status_cmd():
    """Show the active broker and whether it authenticates."""
    from src.brokers import broker_status
    import json as _json
    print(_json.dumps(broker_status(), indent=2, default=str))


@broker_app.command("use")
def broker_use(name: str = typer.Argument(..., help="upstox | groww")):
    """Persist the active broker to .cache/active_broker.txt (either/or)."""
    name = name.lower()
    if name not in ("upstox", "groww"):
        print("broker must be 'upstox' or 'groww'"); raise typer.Exit(1)
    from config import settings
    (settings.cache_dir / "active_broker.txt").write_text(name)
    print(f"Active broker → {name}")


@broker_app.command("test")
def broker_test(verbose: bool = False):
    """Hit every endpoint of the ACTIVE broker and report what comes back.
    Use this to validate Groww's endpoint paths against your real account."""
    import json as _json
    from datetime import date, timedelta
    from src.brokers import get_broker
    from config import settings

    print(f"Active broker: {settings.broker}")
    try:
        c = get_broker()
    except Exception as e:
        print(f"✗ auth: {type(e).__name__}: {e}")
        raise typer.Exit(1)

    def _probe(label, fn):
        try:
            out = fn()
            if isinstance(out, list):
                print(f"  ✓ {label:18s} → {len(out)} rows" +
                      (f"  e.g. {out[0]}" if (verbose and out) else ""))
            elif hasattr(out, "empty"):   # DataFrame
                print(f"  ✓ {label:18s} → {0 if out.empty else len(out)} candles")
            elif isinstance(out, dict):
                keys = list(out.keys())
                print(f"  ✓ {label:18s} → dict keys={keys[:8]}" +
                      (f"\n      {_json.dumps(out, default=str)[:400]}" if verbose else ""))
            else:
                print(f"  ✓ {label:18s} → {str(out)[:120]}")
        except Exception as e:
            print(f"  ✗ {label:18s} → {type(e).__name__}: {e}")

    _probe("profile", c.profile)
    _probe("funds", c.funds)
    _probe("holdings", c.holdings)
    _probe("positions", c.positions)
    _probe("trades_today", c.trades_today)
    _probe("ltp(RELIANCE)", lambda: c.ltp(["RELIANCE.NS"]))
    _probe("quote(RELIANCE)", lambda: c.quote(["RELIANCE.NS"]))
    _probe("candles(RELIANCE)",
           lambda: c.candles("RELIANCE.NS", "day",
                             from_date=date.today() - timedelta(days=30)))
    print("\nLegend: ✓ endpoint reachable+parsed · ✗ failed (fix the path/field "
          "in src/brokers/groww_client.py if Groww).")


def _df_to_table(df, title=""):
    t = Table(title=title)
    for c in df.columns:
        t.add_column(str(c))
    for _, row in df.iterrows():
        t.add_row(*[f"{v:.2f}" if isinstance(v, float) else str(v) for v in row.values])
    return t


# ---------- auth ----------
@auth_app.command("login")
def auth_login():
    from src.upstox.auth import login
    login()


# ---------- portfolio ----------
@port_app.command("report")
def portfolio_report():
    from src.portfolio import PortfolioManager, ReportBuilder
    snap = PortfolioManager().snapshot()
    print("[bold]Summary[/]:", snap.summary)
    out = ReportBuilder().build(snap)
    print("Wrote:", out)


@port_app.command("risk")
def portfolio_risk(threshold: float = 15.0):
    from src.portfolio import PortfolioManager
    pm = PortfolioManager()
    snap = pm.snapshot()
    df = pm.concentration_risk(snap, threshold)
    if df.empty:
        print(f"No holding above {threshold}% weight.")
    else:
        print(_df_to_table(df, "Concentration risk"))


@port_app.command("optimize")
def portfolio_optimize(
    mode: str = "max_sharpe",        # max_sharpe | min_variance | target_return
    target: float = 0.20,            # used only if mode=target_return (e.g. 0.20 = 20% p.a.)
    max_weight: float = 0.25,
    lookback_days: int = 365,
    include_buylist: str = "",       # optional: universe name to add screener buys as candidates
):
    """Modern Portfolio Theory optimization across current holdings (and optional screener buys)."""
    from src.portfolio import PortfolioManager, PortfolioOptimizer
    pm = PortfolioManager()
    snap = pm.snapshot()
    if snap.holdings.empty:
        print("No holdings to optimize.")
        return

    tickers, value_by_yf = [], {}
    for _, row in snap.holdings.iterrows():
        sym = row.get("tradingsymbol", "")
        ikey = row.get("instrument_token") or row.get("instrument_key") or ""
        yf_t = f"{sym}.NS"
        tickers.append((yf_t, ikey))
        value_by_yf[yf_t] = float(row.get("current_value", 0))

    if include_buylist:
        from src.screener import ScreenerEngine
        buy = ScreenerEngine().scan(include_buylist)
        if not buy.empty:
            for _, r in buy.head(15).iterrows():
                t = (r["yf_ticker"], r["instrument_key"])
                if t not in tickers:
                    tickers.append(t)

    opt = PortfolioOptimizer()
    rets = opt.returns(tickers, lookback_days=lookback_days)
    if rets.empty or rets.shape[1] < 2:
        print("Insufficient overlapping history.")
        return

    if mode == "max_sharpe":
        res = opt.max_sharpe(rets, max_weight=max_weight)
    elif mode == "min_variance":
        res = opt.min_variance(rets, max_weight=max_weight)
    else:
        res = opt.target_return(rets, target=target, max_weight=max_weight)

    print(f"\n[bold]{mode}[/]  expected return {res.expected_return*100:.2f}% | "
          f"vol {res.volatility*100:.2f}% | Sharpe {res.sharpe:.2f}")
    w = (res.weights * 100).round(2).rename("weight_pct").reset_index()
    w.columns = ["ticker", "weight_pct"]
    print(_df_to_table(w, "Optimal weights"))

    rebal = opt.rebalance_suggestion(value_by_yf, res)
    if not rebal.empty:
        print(_df_to_table(rebal.reset_index().rename(columns={"index": "ticker"}),
                           "Suggested rebalance"))


@port_app.command("frontier")
def portfolio_frontier(points: int = 20, max_weight: float = 0.25, lookback_days: int = 365):
    """Sample the efficient frontier across current holdings."""
    from src.portfolio import PortfolioManager, PortfolioOptimizer
    pm, opt = PortfolioManager(), PortfolioOptimizer()
    snap = pm.snapshot()
    if snap.holdings.empty:
        print("No holdings.")
        return
    tickers = [(f"{r.tradingsymbol}.NS", r.get("instrument_token", "")) for _, r in snap.holdings.iterrows()]
    rets = opt.returns(tickers, lookback_days=lookback_days)
    if rets.empty:
        print("No data.")
        return
    df = opt.efficient_frontier(rets, points=points, max_weight=max_weight)
    print(_df_to_table(df.round(4), "Efficient frontier"))


@port_app.command("losers")
def portfolio_losers(threshold: float = -10.0):
    from src.portfolio import PortfolioManager
    pm = PortfolioManager()
    snap = pm.snapshot()
    df = pm.underperformers(snap, threshold)
    print(_df_to_table(df, f"Underperformers (< {threshold}%)") if not df.empty else "None.")


# ---------- screener ----------
@scr_app.command("scan")
def screener_scan(
    universe: str = "all_nse",
    tech_min: float = 60.0,
    fund_min: float = 50.0,
    top: int = 30,
):
    """Two-stage funnel: technical scan over the whole universe → fundamental scan on survivors."""
    from src.screener import ScreenerEngine
    df = ScreenerEngine().scan(universe, tech_min=tech_min, fund_min=fund_min)
    if df.empty:
        print("No survivors. Try lowering --tech-min / --fund-min.")
        return
    cols = ["name", "symbol", "ltp", "combined", "tech_score", "fund_score",
            "recommendation", "rsi", "ret_3m_pct", "PE", "ROE", "sector"]
    df = df[[c for c in cols if c in df.columns]].head(top)
    print(_df_to_table(df, f"Screener — {universe} (tech≥{tech_min}, fund≥{fund_min})"))


@scr_app.command("technical")
def screener_technical(universe: str = "all_nse", tech_min: float = 60.0, top: int = 50):
    """Stage-1 only: technical scan over the full universe."""
    from src.screener import ScreenerEngine
    df = ScreenerEngine().technical_scan(universe, tech_min)
    if df.empty:
        print("No survivors.")
        return
    print(_df_to_table(df.head(top), f"Technical survivors — {universe}"))


@scr_app.command("refresh-instruments")
def refresh_instruments(exchange: str = "NSE"):
    """Force-refresh the Upstox instrument master."""
    from src.data.instruments import load_instruments
    df = load_instruments(exchange, refresh=True)
    print(f"Cached {len(df):,} {exchange} instruments.")


# ---------- intraday ----------
@intra_app.command("analyze")
def intraday_analyze(days: int = 90):
    from src.intraday import IntradayAnalyzer
    r = IntradayAnalyzer().analyze(days)
    print(r)


@intra_app.command("scan")
def intraday_scan(universe: str = "nifty50", min_score: int = 40):
    from src.intraday import IntradayScanner
    df = IntradayScanner().scan(universe, min_score)
    if df.empty:
        print("No opportunities meeting threshold right now.")
    else:
        print(_df_to_table(df.drop(columns=["signals"]).round(2), "Intraday opportunities"))
        for _, row in df.iterrows():
            print(f"  • {row['symbol']} {row['direction']} — signals: {', '.join(row['signals'])}")


# ---------- agent ----------
@agent_app.command("portfolio")
def agent_portfolio(question: str):
    from src.agents import PortfolioAgent
    print(PortfolioAgent().run(question))


@agent_app.command("screener")
def agent_screener(question: str):
    from src.agents import ScreenerAgent
    print(ScreenerAgent().run(question))


@agent_app.command("intraday")
def agent_intraday(question: str):
    from src.agents import IntradayAgent
    print(IntradayAgent().run(question))


# ---------- telegram ----------
@tg_app.command("authorize")
def tg_authorize(chat_id: int):
    """Manually whitelist a Telegram chat id."""
    from src.telegram.bot import authorize_chat
    authorize_chat(chat_id)
    print(f"Authorized chat_id {chat_id}")


@tg_app.command("send-report")
def tg_send_report(chat_id: int = 0):
    """Build the portfolio Excel report and push it to Telegram (all authorized chats by default)."""
    from src.portfolio import PortfolioManager, ReportBuilder
    from src.telegram.bot import TelegramBot
    snap = PortfolioManager().snapshot()
    paths = ReportBuilder().build(snap)
    bot = TelegramBot()
    caption = (
        f"Invested ₹{snap.summary['holdings_invested']:.0f} | "
        f"Value ₹{snap.summary['holdings_value']:.0f} | "
        f"P&L ₹{snap.summary['holdings_pnl']:.0f} "
        f"({snap.summary['holdings_pnl_pct']:.2f}%)"
    )
    if chat_id:
        bot.send_document(chat_id, paths["xlsx"], caption=caption)
    else:
        bot.broadcast_document(paths["xlsx"], caption=caption)
    print("Sent.")


@tg_app.command("send")
def tg_send(text: str, chat_id: int = 0):
    """Send an arbitrary text message to Telegram."""
    from src.telegram.bot import TelegramBot
    bot = TelegramBot()
    if chat_id:
        bot.send_message(chat_id, text)
    else:
        bot.broadcast(text)


# ---------- quant (D-R1-Quant funnel) ----------
@quant_app.command("run")
def quant_run(
    universe: str = "nifty50",
    live_orders: bool = False,
    result_file: str = "",   # if set, dump JSON result here when done
):
    """Run the full Stage 1→4 funnel (macro → technical → intel → MPT → intraday).

    Designed to be safe to launch from a subprocess: all output goes to stdout
    line-buffered, and the final result is optionally dumped to `result_file`
    so the parent process can read it.
    """
    import sys
    import json as _json
    from src.agents.quant_agent import DR1QuantAgent
    print(f"[CLI] quant run · universe={universe} · live_orders={live_orders}",
          flush=True, file=sys.stderr)
    try:
        res = DR1QuantAgent(universe=universe, live_orders=live_orders).run()
        if result_file:
            Path(result_file).write_text(_json.dumps(res, default=str, indent=2))
            print(f"[CLI] result → {result_file}", flush=True, file=sys.stderr)
        print(_json.dumps(res, default=str, indent=2))
    except Exception as e:
        import traceback
        print(f"[CLI] FATAL: {type(e).__name__}: {e}", flush=True, file=sys.stderr)
        print(traceback.format_exc(), flush=True, file=sys.stderr)
        raise typer.Exit(code=1)


@quant_app.command("universe-map")
def quant_universe_map(universe: str = "all_nse", max_age_days: float = 7.0):
    """Build/refresh the Universe Map — crawl every stock, score it, ingest into KB.

    Incremental: stocks with KB data younger than --max-age-days are reused.
    First run is long; later runs only refresh stale names.
    """
    from src.universe_map import build_universe_map
    res = build_universe_map(universe=universe, max_age_days=max_age_days)
    if res.get("error"):
        print(f"ERROR: {res['error']}")
        return
    print(f"Wrote {res['count']} stocks · {res['tech_total']} technically scored · "
          f"{res['fund_scanned']} freshly fetched · {res['fund_reused']} reused from KB")


@quant_app.command("debug-nse")
def quant_debug_nse(
    symbol: str = typer.Argument("RELIANCE", help="NSE trading symbol"),
):
    """Dump EVERY NSE endpoint's raw JSON to .cache/nse_debug/<symbol>/ and
    print a summary. This is how we see NSE's actual response structure so the
    scraper can be written to match it exactly."""
    import json as _json
    from src.data.nse_scraper import debug_dump
    summary = debug_dump(symbol)
    print(_json.dumps(summary, indent=2, default=str))
    print(f"\nRaw JSON files saved under: {summary['dump_dir']}")
    print("If parsed_derived_financials is still empty, open "
          "financial_results_quarterly.json there and paste the first item.")


@quant_app.command("debug-fundamentals")
def quant_debug_fundamentals(
    symbol: str = typer.Argument("RELIANCE", help="NSE trading symbol"),
):
    """Show what EACH fundamentals source returns for one stock — NSE, Yahoo,
    screener.in, and the final merged result. This pinpoints which source is
    failing when fund scores come back empty."""
    import json as _json
    sym = symbol.upper()

    def _show(label, fn):
        print(f"\n{'='*60}\n  {label}\n{'='*60}")
        try:
            data = fn()
            if not data:
                print("  (empty — source returned nothing)")
            else:
                print(_json.dumps(data, indent=2, default=str))
        except Exception as e:
            print(f"  ✗ {type(e).__name__}: {e}")

    from src.data.nse_fundamentals import fetch_nse_fundamentals
    from src.tools.yahoo_fundamentals import fetch_fundamentals_yahoo
    from src.tools.screener_in import _screener_only, fetch_fundamentals

    _show("NSE India  (/api/quote-equity)", lambda: fetch_nse_fundamentals(sym))
    _show("Yahoo Finance  (quoteSummary)", lambda: fetch_fundamentals_yahoo(sym))
    _show("screener.in  (HTML scrape)", lambda: _screener_only(sym))
    _show("MERGED  (what the funnel/map actually uses)", lambda: fetch_fundamentals(sym))


@quant_app.command("debug-scan-row")
def quant_debug_scan_row(
    symbol: str = typer.Argument("RELIANCE", help="NSE trading symbol"),
):
    """Trace ONE stock through the exact same path the universe-map / funnel
    technical scan uses. This is the surgical 'why is this returning empty'
    debugger."""
    import pickle as _pkl
    from src.data.instruments import resolve_instrument_key
    from src.data.instrument_blacklist import is_blacklisted, _load
    from src.data.cache import _path as cache_path
    from src.data import MarketData
    from src.screener.technical import technical_score

    sym = symbol.upper()
    print(f"\n— Step 1: resolve_instrument_key('{sym}') —")
    ikey = resolve_instrument_key(sym)
    print(f"   → instrument_key: {ikey}")
    if not ikey:
        print("   ✗ ABORT — symbol not in Upstox instrument dump."); return

    print(f"\n— Step 2: blacklist check —")
    bl = _load()
    print(f"   blacklist size: {len(bl)} entries")
    if is_blacklisted(ikey):
        print(f"   ✗ {ikey} IS blacklisted — every call short-circuits to empty.")
        print(f"     fix: rm .cache/instrument_blacklist.json   (or click 🧹 Reset)")
        return
    print(f"   ✓ not blacklisted")

    print(f"\n— Step 3: daily-candle cache file —")
    cf = cache_path("daily", f"daily_{ikey}_400")
    if cf.exists():
        cached = _pkl.loads(cf.read_bytes())
        is_df = hasattr(cached, "shape")
        if is_df:
            print(f"   cache file exists, {cached.shape[0]} rows")
            if cached.empty:
                print(f"   ⚠ cached DataFrame is EMPTY — will refetch")
        else:
            print(f"   cache file exists but is not a DataFrame: {type(cached)}")
    else:
        print(f"   no cache file (will fetch fresh)")

    print(f"\n— Step 4: MarketData.daily(...) —")
    md = MarketData()
    df = md.daily(f"{sym}.NS", ikey, lookback_days=400)
    print(f"   returned: {'empty' if df.empty else f'{len(df)} rows ({df.index[0]} → {df.index[-1]})'}")
    if df.empty:
        print(f"   ✗ ABORT — daily() returned empty. Earlier steps look OK so this is a real Upstox call problem.")
        print(f"     Run:  python main.py quant debug-upstox-candles {sym}")
        return

    print(f"\n— Step 5: technical_score() —")
    score = technical_score(df)
    print(f"   score: {score.get('score')}")
    print(f"   rsi:   {score.get('rsi')}")
    print(f"   above EMA200: {score.get('price_above_ema200')}")
    print(f"   above EMA50:  {score.get('price_above_ema50')}")
    print(f"\n✓ Trace complete — this stock would survive any tech_min ≤ {score.get('score')}.")


@quant_app.command("clear-blacklist")
def quant_clear_blacklist():
    """Delete the instrument blacklist — useful if it has been poisoned with
    transient failures from earlier buggy runs."""
    from config import settings as _s
    p = _s.cache_dir / "instrument_blacklist.json"
    if p.exists():
        size = p.stat().st_size
        p.unlink()
        print(f"✓ removed {p}  ({size} bytes)")
    else:
        print(f"(nothing to remove at {p})")
    # Also clear the daily cache so empty DataFrames don't keep being served
    import shutil
    d = _s.cache_dir / "daily"
    if d.exists():
        n = sum(1 for _ in d.rglob("*"))
        shutil.rmtree(d)
        print(f"✓ removed {d}  ({n} files)")


@quant_app.command("debug-upstox-candles")
def quant_debug_upstox_candles(
    symbol: str = typer.Argument("RELIANCE", help="NSE trading symbol"),
    lookback_days: int = 30,
):
    """Hit Upstox /historical-candle for ONE stock and dump the raw response.

    This is what we need to diagnose 'every candle returns empty' — it tests
    the candle endpoint directly, separate from auth (which is what
    profile() tests).
    """
    import json as _json
    import urllib.parse
    from datetime import date, timedelta

    import requests

    from src.data.instruments import resolve_instrument_key
    from src.upstox.client import UpstoxClient

    sym = symbol.upper()
    ikey = resolve_instrument_key(sym)
    print(f"\n— Symbol resolution —")
    print(f"  {sym} → instrument_key: {ikey}")
    if not ikey:
        print("  ✗ No instrument_key found. Check that this symbol exists in the Upstox dump.")
        return

    print(f"\n— Auth check —")
    try:
        client = UpstoxClient()
        prof = client.profile()
        print(f"  ✓ {prof.get('user_name') or prof.get('email')}")
    except Exception as e:
        print(f"  ✗ auth fail: {e}")
        return

    to_date = date.today()
    from_date = to_date - timedelta(days=lookback_days)
    base = "https://api.upstox.com/v2"

    # Try BOTH raw and URL-encoded paths — Upstox uses pipe in instrument_key
    # which some HTTP clients re-encode and some don't.
    variants = [
        ("raw pipe",      f"{base}/historical-candle/{ikey}/day/{to_date}/{from_date}"),
        ("encoded pipe",  f"{base}/historical-candle/{urllib.parse.quote(ikey, safe='')}/day/{to_date}/{from_date}"),
    ]
    for label, url in variants:
        print(f"\n— Request ({label}) —")
        print(f"  GET {url}")
        try:
            r = requests.get(url, headers=client._headers, timeout=30)
            print(f"  status: {r.status_code}")
            print(f"  headers: content-type={r.headers.get('content-type')}, "
                  f"x-ratelimit-remaining={r.headers.get('x-ratelimit-remaining')}, "
                  f"retry-after={r.headers.get('retry-after')}")
            body = r.text
            print(f"  body (first 800 chars):")
            print("  " + body[:800].replace("\n", "\n  "))
            try:
                j = r.json()
                candles = ((j.get("data") or {}).get("candles")) or []
                print(f"  → parsed candles count: {len(candles)}")
                if candles:
                    print(f"  → first candle: {candles[0]}")
                    print(f"  → last candle:  {candles[-1]}")
            except Exception as e:
                print(f"  → JSON parse error: {e}")
        except Exception as e:
            print(f"  ✗ request error: {type(e).__name__}: {e}")


@quant_app.command("debug-yahoo")
def quant_debug_yahoo(
    symbol: str = typer.Argument("RELIANCE", help="NSE trading symbol e.g. RELIANCE"),
):
    """Live Yahoo Finance fetch + parse for an Indian stock. Bypasses cache.

    Use this to verify Yahoo's API is reachable from your network.
    """
    import json as _json
    import requests
    from src.tools.yahoo_fundamentals import fetch_fundamentals_yahoo, _fetch_raw

    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    print(f"\n— Reachability test —")
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            r = requests.get(f"https://{host}/", timeout=10,
                             headers={"User-Agent": "Mozilla/5.0"})
            print(f"  {host:30s} → HTTP {r.status_code}")
        except Exception as e:
            print(f"  {host:30s} → {type(e).__name__}: {e}")

    print(f"\n— Live fetch for {sym}.NS —")
    raw = _fetch_raw(f"{sym}.NS")
    print(f"  raw keys: {list(raw.keys()) if raw else '(empty)'}")

    print(f"\n— Normalised (cache cleared) —")
    # bypass cache by clearing the key
    from config import settings as _s
    cf = _s.cache_dir / "yahoo_fundamentals" / f"{sym}.pkl"
    if cf.exists():
        cf.unlink()
    parsed = fetch_fundamentals_yahoo(sym)
    print(_json.dumps(parsed, indent=2, default=str))


@quant_app.command("debug-screener")
def quant_debug_screener(
    symbol: str = typer.Argument("RELIANCE", help="NSE trading symbol, e.g. RELIANCE, HDFCBANK, PRIMECAB"),
    brief: bool = True,
):
    """Live screener.in fetch + parse, with diagnostics. Bypasses cache."""
    import json as _json
    from src.tools.screener_in import debug_fetch
    res = debug_fetch(symbol)
    if brief:
        # Strip the bulky raw-html field for a readable terminal print
        for k in ("ratio_container_snippet", "market_cap_context", "html_start"):
            if k in res and isinstance(res[k], str) and len(res[k]) > 400:
                res[k] = res[k][:400] + f"…[truncated, full saved to {res.get('full_html_saved')}]"
    print(_json.dumps(res, indent=2, default=str))


@quant_app.command("macro")
def quant_macro():
    """One-shot macro snapshot (India VIX / PCR / USDINR → market mode)."""
    from src.tools import MacroSnapshot
    print(MacroSnapshot().market_mode())


@quant_app.command("scan-technical")
def quant_scan_tech(universe: str = "nifty50", top: int = 20):
    """Stage-1 TA-Lib technical scan only."""
    from src.screener.talib_screener import TALibScreener
    df = TALibScreener().scan(universe, top_n=top)
    print(df if df.empty else _df_to_table(df, "TA-Lib candidates"))


@quant_app.command("schedule")
def quant_schedule():
    """Start APScheduler — runs macro/funnel/intraday/EOD on IST market hours."""
    from src.scheduler import run_blocking
    run_blocking()


@quant_app.command("init-db")
def quant_init_db():
    """Apply TimescaleDB schema (requires TIMESCALE_DSN)."""
    import os
    import subprocess
    from config import settings
    if not settings.timescale_dsn:
        print("TIMESCALE_DSN not set in .env"); return
    schema = Path(__file__).parent / "src" / "db" / "schema.sql"
    subprocess.check_call(["psql", settings.timescale_dsn, "-f", str(schema)])


@app.command("dashboard")
def dashboard(host: str = "127.0.0.1", port: int = 8000, reload: bool = False):
    """Launch the web dashboard at http://127.0.0.1:8000"""
    import uvicorn
    uvicorn.run("src.dashboard.app:app", host=host, port=port, reload=reload)


@tg_app.command("bot")
def tg_bot():
    """Run the long-polling bot. Responds to /report, /screener, /optimize, /intraday, /agent."""
    from src.telegram.bot import CommandBot
    from src.telegram.handlers import HANDLERS
    CommandBot(HANDLERS).run()


if __name__ == "__main__":
    app()

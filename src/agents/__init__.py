import sys as _sys, time as _time
_t0 = _time.time()
def _p(m):
    print(f"[IMPORT +{_time.time()-_t0:5.2f}s] agents: {m}",
          flush=True, file=_sys.stderr)

_p("portfolio_agent");   from .portfolio_agent import PortfolioAgent  # noqa: F401
_p("screener_agent");    from .screener_agent import ScreenerAgent    # noqa: F401
_p("intraday_agent");    from .intraday_agent import IntradayAgent    # noqa: F401
_p("ready")

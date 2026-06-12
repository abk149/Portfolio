import sys as _sys, time as _time
_t = _time.time()
def _p(m): print(f"[IMPORT +{_time.time()-_t:5.2f}s] tools: {m}",
                 flush=True, file=_sys.stderr)
_p("web_search");      from .web_search import WebSearcher  # noqa: F401
_p("pdf_extractor");   from .pdf_extractor import PDFExtractor  # noqa: F401
_p("macro");           from .macro import MacroSnapshot  # noqa: F401
_p("quant_calculator");from .quant_calculator import QuantCalculator  # noqa: F401
_p("upstox_bridge");   from .upstox_bridge import UpstoxBridge  # noqa: F401
_p("screener_in");     from . import screener_in   # noqa: F401
_p("done")

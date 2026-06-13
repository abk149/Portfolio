"""Mobile / Desktop either-or gate (ADDITIVE — does not touch existing routes).

A single flag file decides who is allowed to use the system:

    .cache/ui_mode.txt   →   "desktop"  (default)  or  "mobile"

  • desktop mode  → the Mac browser dashboard works normally; the phone app
                    is locked out (it sends header X-Client: portfolio-mobile,
                    which we 403 in desktop mode).
  • mobile  mode  → the phone app works; the Mac browser dashboard `/` is
                    replaced by a "switch to phone" notice. The JSON API stays
                    open so the phone can use it.

Toggle it three ways, all live (no restart needed):
  • POST /api/mode  {"mode": "mobile"|"desktop"}     (from phone or Mac)
  • the Toggle_Mobile_Mode.command script on the Desktop
  • editing .cache/ui_mode.txt by hand

The phone app always sends `X-Client: portfolio-mobile`; the web browser does
not — that header is the only thing distinguishing the two clients.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config import settings

MODE_FILE: Path = settings.cache_dir / "ui_mode.txt"
MOBILE_HEADER = "x-client"
MOBILE_VALUE = "portfolio-mobile"


def get_mode() -> str:
    try:
        m = MODE_FILE.read_text().strip().lower()
        return "mobile" if m == "mobile" else "desktop"
    except Exception:
        return "desktop"


def set_mode(mode: str) -> str:
    mode = "mobile" if str(mode).lower() == "mobile" else "desktop"
    MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODE_FILE.write_text(mode)
    return mode


# Ensure the flag exists (default desktop) without clobbering an existing value
if not MODE_FILE.exists():
    set_mode("desktop")


_MOBILE_ONLY_NOTICE = """
<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mobile mode active</title>
<style>
 body{font-family:-apple-system,Segoe UI,sans-serif;background:#0d1117;color:#e6edf3;
   display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
 .box{max-width:460px;text-align:center;padding:32px;border:1px solid #2a3140;
   border-radius:14px;background:#161b22}
 h1{font-size:20px;margin:0 0 12px} p{color:#8b949e;line-height:1.6}
 code{background:#1c2330;padding:2px 6px;border-radius:5px}
 .pill{display:inline-block;margin-top:16px;padding:8px 16px;border-radius:999px;
   background:#2f81f7;color:#fff;font-weight:600;cursor:pointer;border:none;font-size:14px}
</style></head><body>
<div class="box">
 <h1>📱 Mobile mode is active</h1>
 <p>The Mac dashboard is locked because the system is in <b>mobile mode</b>.
    Use the <b>Portfolio Quant</b> app on your phone.</p>
 <p>To use the Mac dashboard again, run <code>Toggle_Mobile_Mode.command</code>
    or press the button below.</p>
 <button class="pill" onclick="fetch('/api/mode',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:'desktop'})}).then(()=>location.reload())">
   Switch to Desktop mode
 </button>
</div></body></html>
"""


class ModeGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        # Always allow the mode endpoint + static assets needed by the notice
        if path == "/api/mode":
            return await call_next(request)

        mode = get_mode()
        is_mobile_client = (
            request.headers.get(MOBILE_HEADER, "").lower() == MOBILE_VALUE
        )

        if mode == "mobile":
            # Block the human web UI (top-level page + its static bundle).
            # Everything else (the JSON API the phone needs) passes through.
            if not is_mobile_client and (path == "/" or path.startswith("/static")):
                return HTMLResponse(_MOBILE_ONLY_NOTICE, status_code=200)
            return await call_next(request)

        # desktop mode → lock the phone app out
        if is_mobile_client:
            return JSONResponse(
                {"error": "desktop_mode_active",
                 "message": "The Mac is in Desktop mode. Run "
                            "Toggle_Mobile_Mode.command (or toggle from the "
                            "Mac dashboard Settings) to switch to Mobile mode."},
                status_code=403,
            )
        return await call_next(request)


def install_mode_gate(app: FastAPI) -> None:
    app.add_middleware(ModeGateMiddleware)

    @app.get("/api/mode")
    def _api_get_mode():
        return {"mode": get_mode()}

    @app.post("/api/mode")
    def _api_set_mode(body: dict):
        return {"mode": set_mode(body.get("mode", "desktop"))}

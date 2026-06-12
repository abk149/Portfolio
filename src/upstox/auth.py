"""Upstox OAuth2 login.

Run `python -m src.upstox.auth` to perform a one-time interactive login.
Token is cached to `settings.upstox_token_file` and auto-loaded by `UpstoxClient`.
Upstox access tokens expire daily ~3:30 AM IST; re-run when expired.
"""
from __future__ import annotations

import json
import http.server
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from typing import Optional

import requests

from config import settings
from src.utils.logger import get_logger

log = get_logger("upstox.auth")

AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"


def _save(token: dict) -> None:
    token["fetched_at"] = datetime.now(timezone.utc).isoformat()
    settings.upstox_token_file.write_text(json.dumps(token, indent=2))
    log.info(f"Saved token → {settings.upstox_token_file}")


def load_token() -> Optional[dict]:
    if not settings.upstox_token_file.exists():
        return None
    return json.loads(settings.upstox_token_file.read_text())


def build_auth_url() -> str:
    """Return the Upstox login URL the user should open in a browser."""
    import urllib.parse
    params = {
        "client_id": settings.upstox_api_key,
        "redirect_uri": settings.upstox_redirect_uri,
        "response_type": "code",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_and_save(code: str) -> dict:
    """Public helper — exchange an auth code for an access token and persist it."""
    token = _exchange_code(code)
    _save(token)
    return token


def _exchange_code(code: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.upstox_api_key,
            "client_secret": settings.upstox_api_secret,
            "redirect_uri": settings.upstox_redirect_uri,
            "grant_type": "authorization_code",
        },
        headers={"accept": "application/json", "Api-Version": "2.0"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def login() -> dict:
    if not (settings.upstox_api_key and settings.upstox_api_secret):
        raise RuntimeError("Set UPSTOX_API_KEY / UPSTOX_API_SECRET in .env first.")

    parsed = urllib.parse.urlparse(settings.upstox_redirect_uri)
    host, port = parsed.hostname or "localhost", parsed.port or 8765
    code_holder: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.urlparse(self.path).query
            code_holder.update(urllib.parse.parse_qs(q))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>Upstox login OK. Return to terminal.</h2>")

        def log_message(self, *a):  # silence
            return

    srv = http.server.HTTPServer((host, port), Handler)
    threading.Thread(target=srv.handle_request, daemon=True).start()

    params = {
        "client_id": settings.upstox_api_key,
        "redirect_uri": settings.upstox_redirect_uri,
        "response_type": "code",
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    log.info(f"Opening browser for Upstox login: {url}")
    webbrowser.open(url)

    while "code" not in code_holder:
        pass
    code = code_holder["code"][0]
    token = _exchange_code(code)
    _save(token)
    return token


if __name__ == "__main__":
    login()

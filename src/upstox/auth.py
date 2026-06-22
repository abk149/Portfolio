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
    path = settings.upstox_token_file
    # Ensure the parent dir exists — on a fresh Android install .cache/ may not
    # exist yet, and Path.write_text does NOT create it (the exchange would
    # succeed at Upstox but then fail to persist, looking like a login error).
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token, indent=2))
    log.info(f"Saved token → {path}")


def load_token() -> Optional[dict]:
    # Priority 1: Direct bearer token from environment (injected via Android settings)
    import os
    env_token = os.getenv("UPSTOX_BEARER_TOKEN")
    if env_token and env_token.strip():
        token = env_token.strip()
        # Clean "Bearer " prefix if user accidentally included it
        if token.lower().startswith("bearer "):
            token = token[7:].strip()

        print(f"[Upstox Auth] Using DIRECT BEARER TOKEN (len={len(token)})")
        return {"access_token": token}

    # Priority 2: Cached token file
    if not settings.upstox_token_file.exists():
        return None
    try:
        return json.loads(settings.upstox_token_file.read_text())
    except Exception as e:
        print(f"[Upstox Auth] Failed to read token file: {e}")
        return None


def build_auth_url() -> str:
    """Return the Upstox login URL the user should open in a browser."""
    import urllib.parse

    # Strip stray surrounding quotes/whitespace (common when creds are pasted
    # straight from a quoted .env line) — these cause Upstox UDAPI100068.
    key = settings.upstox_api_key.strip().strip('"').strip("'").strip()
    uri = settings.upstox_redirect_uri.strip().strip('"').strip("'").strip()

    # ── DEBUG LOGGING ──────────────────────────────────────────────────────
    print(f"[Upstox Auth] Building URL.")
    print(f"  > client_id:    {repr(key)}")
    print(f"  > redirect_uri: {repr(uri)}")

    if not key:
        raise ValueError("UPSTOX_API_KEY (client_id) is empty — set it before login.")

    params = {
        "client_id": key,
        "redirect_uri": uri,
        "response_type": "code",
    }

    # Use the fixed, absolute Upstox login host — NOT settings.upstox_base_url.
    # A blank/misconfigured base_url must never be able to produce a relative
    # (dead) login URL. This mirrors the working Upstox bot exactly.
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    print(f"[Upstox Auth] Final generated URL: {url}")
    return url


def exchange_and_save(code: str) -> dict:
    """Public helper — exchange an auth code for an access token and persist it."""
    token = _exchange_code(code)
    _save(token)
    # A stale UPSTOX_BEARER_TOKEN (Priority 1 in load_token) would shadow the
    # token we just saved and cause UDAPI100050. OAuth and direct-bearer are
    # mutually exclusive — drop the env bearer so the fresh OAuth token wins.
    import os
    os.environ.pop("UPSTOX_BEARER_TOKEN", None)
    return token


def _clean(v: str) -> str:
    """Strip stray surrounding quotes/whitespace (common when creds are pasted
    from a quoted .env line). Mismatched/quoted creds cause 401 on exchange."""
    return (v or "").strip().strip('"').strip("'").strip()


def _exchange_code(code: str) -> dict:
    # Use the fixed absolute token endpoint (not base_url, which may be blank).
    # The redirect_uri here MUST exactly equal the one used in the login dialog
    # and registered with Upstox, or the exchange returns 401.
    token_url = TOKEN_URL

    client_id = _clean(settings.upstox_api_key)
    client_secret = _clean(settings.upstox_api_secret)
    redirect_uri = _clean(settings.upstox_redirect_uri)
    code = _clean(code)

    print(f"[Upstox Auth] Exchanging code at: {token_url}")
    print(f"  > client_id:    {repr(client_id)}")
    print(f"  > redirect_uri: {repr(redirect_uri)}")
    print(f"  > secret set:   {bool(client_secret)} (len={len(client_secret)})")

    resp = requests.post(
        token_url,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        headers={"accept": "application/json", "Api-Version": "2.0"},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"[Upstox Auth] Exchange FAILED: {resp.status_code} {resp.text}")
        # Surface Upstox's own message (e.g. invalid client_secret) to the UI.
        raise RuntimeError(f"Upstox token exchange failed (HTTP {resp.status_code}): {resp.text}")
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

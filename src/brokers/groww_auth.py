"""Groww authentication.

Groww's Trading API does not use an OAuth redirect like Upstox. There are two
supported ways to get an access token (https://groww.in/trade-api/docs):

  1. Direct daily access token — generated in the Groww web console and pasted
     in (simplest; like Upstox's "direct bearer token"). Set GROWW_ACCESS_TOKEN
     or paste it in the app.
  2. API key + secret + TOTP — the SDK generates a daily token from an API key
     and a TOTP seed. We support this when `pyotp` is available.

Token resolution order (mirrors Upstox's load_token):
  env GROWW_ACCESS_TOKEN  →  cached token file  →  API-key/secret/TOTP
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

import requests

from config import settings
from src.utils.logger import get_logger

log = get_logger("brokers.groww_auth")

# ── Groww Trading API endpoints (verify against current docs; isolated here) ──
GROWW_BASE = "https://api.groww.in"
TOKEN_ENDPOINT = f"{GROWW_BASE}/v1/token/api/access"   # api-key + checksum → token


def _save(token: str) -> None:
    settings.groww_token_file.parent.mkdir(parents=True, exist_ok=True)
    settings.groww_token_file.write_text(json.dumps(
        {"access_token": token, "fetched_at": datetime.now(timezone.utc).isoformat()},
        indent=2))
    log.info(f"Saved Groww token → {settings.groww_token_file}")


def save_token(token: str) -> str:
    token = (token or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    _save(token)
    return token


def _from_api_key_totp() -> Optional[str]:
    """Generate a daily token from API key + secret (TOTP). Best-effort —
    requires `pyotp`. Returns None if not configured/available."""
    key, secret = settings.groww_api_key, settings.groww_api_secret
    if not (key and secret):
        return None
    try:
        import pyotp  # optional dep
    except ImportError:
        log.info("groww: pyotp not installed — skipping API-key/TOTP token path")
        return None
    try:
        totp = pyotp.TOTP(secret).now()
        r = requests.post(
            TOKEN_ENDPOINT,
            json={"key_type": "totp", "totp": totp},
            headers={"Authorization": f"Bearer {key}",
                     "Accept": "application/json",
                     "X-API-VERSION": "1.0"},
            timeout=20,
        )
        if r.status_code == 200:
            tok = (r.json().get("token") or r.json().get("access_token")
                   or (r.json().get("data") or {}).get("token"))
            if tok:
                _save(tok)
                return tok
        log.warning(f"groww token endpoint → {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.warning(f"groww TOTP token generation failed: {e}")
    return None


def load_token() -> Optional[str]:
    # 1. Direct token from env (injected by the Android layer or .env)
    env_tok = os.getenv("GROWW_ACCESS_TOKEN")
    if env_tok and env_tok.strip():
        tok = env_tok.strip()
        return tok[7:].strip() if tok.lower().startswith("bearer ") else tok

    # 2. Cached token file
    try:
        if settings.groww_token_file.exists():
            data = json.loads(settings.groww_token_file.read_text())
            if data.get("access_token"):
                return data["access_token"]
    except Exception as e:
        log.debug(f"groww token file read failed: {e}")

    # 3. API key + secret + TOTP
    return _from_api_key_totp()

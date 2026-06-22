"""Groww authentication — robust, mirroring the Upstox auth contract.

Groww has no OAuth redirect. A daily access token is obtained one of three ways
(https://groww.in/trade-api/docs/curl):

  1. Direct daily access token — generated in the Groww console and pasted in
     (like Upstox's "direct bearer token").
  2. API key + TOTP — pyotp turns a base32 TOTP seed into a 6-digit code which
     is exchanged for a daily token.  ← the method this app uses.
  3. API key + secret (checksum) — SHA256(secret + epoch_ts) exchanged for a
     daily token.

Token endpoint (verified against current docs):
    POST https://api.groww.in/v1/token/api/access
    headers: Authorization: Bearer <API_KEY>, X-API-VERSION: 1.0
    body (TOTP):     {"key_type":"totp","totp":"<6-digit>"}
    body (checksum): {"key_type":"approval","checksum":"<sha256>","timestamp":"<epoch>"}
    response: {"token": "...", "expiry": ..., "isActive": true, ...}

Resolution order (mirrors Upstox load_token):
    env GROWW_ACCESS_TOKEN  →  cached token file (same-day)  →  TOTP  →  checksum
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date, datetime, timezone
from typing import Optional

import requests

from config import settings
from src.utils.logger import get_logger

log = get_logger("brokers.groww_auth")

GROWW_BASE = "https://api.groww.in"
TOKEN_ENDPOINT = f"{GROWW_BASE}/v1/token/api/access"


# ── persistence ───────────────────────────────────────────────────────────────
def _save(token: str, meta: Optional[dict] = None) -> None:
    path = settings.groww_token_file
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"access_token": token,
           "fetched_at": datetime.now(timezone.utc).isoformat(),
           "fetched_date": date.today().isoformat()}
    if meta:
        for k in ("expiry", "tokenRefId", "sessionName"):
            if k in meta:
                doc[k] = meta[k]
    path.write_text(json.dumps(doc, indent=2))
    log.info(f"Saved Groww token → {path}")


def save_token(token: str) -> str:
    """Persist a directly-pasted daily token."""
    token = (token or "").strip().strip('"').strip("'").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    _save(token)
    return token


def _cached_token() -> Optional[str]:
    try:
        if not settings.groww_token_file.exists():
            return None
        data = json.loads(settings.groww_token_file.read_text())
        tok = data.get("access_token")
        if not tok:
            return None
        # Groww tokens are daily — only reuse if minted today.
        if data.get("fetched_date") != date.today().isoformat():
            return None
        return tok
    except Exception as e:
        log.debug(f"groww token file read failed: {e}")
        return None


# ── token minting ───────────────────────────────────────────────────────────
def _post_token(api_key: str, body: dict) -> str:
    """POST to the token endpoint; return the token or raise with the real
    Groww error message (so the UI can show exactly what's wrong)."""
    r = requests.post(
        TOKEN_ENDPOINT, json=body,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json",
                 "Accept": "application/json",
                 "X-API-VERSION": "1.0"},
        timeout=20,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Groww token endpoint HTTP {r.status_code}: {r.text[:300]}")
    j = r.json() if r.text else {}
    # Groww wraps most responses as {"status":"SUCCESS","payload":{...}}. The
    # token may be top-level or nested under payload/data.
    payload = j.get("payload") if isinstance(j.get("payload"), dict) else {}
    data = j.get("data") if isinstance(j.get("data"), dict) else {}
    tok = (j.get("token") or j.get("access_token")
           or payload.get("token") or payload.get("access_token")
           or data.get("token") or data.get("access_token"))
    if not tok:
        raise RuntimeError(f"Groww token endpoint returned no token: {str(j)[:300]}")
    meta = payload or data or (j if isinstance(j, dict) else None)
    _save(tok, meta=meta)
    return tok


def mint_via_totp(api_key: str, totp_secret: str) -> str:
    """API key + base32 TOTP seed → daily access token."""
    import pyotp  # optional dep (added to Chaquopy pip)
    api_key = (api_key or "").strip().strip('"').strip("'").strip()
    totp_secret = (totp_secret or "").strip().replace(" ", "")
    if not api_key or not totp_secret:
        raise RuntimeError("Groww API key and TOTP secret are both required.")
    code = pyotp.TOTP(totp_secret).now()
    log.info("groww: minting token via TOTP")
    return _post_token(api_key, {"key_type": "totp", "totp": code})


def mint_via_secret(api_key: str, api_secret: str) -> str:
    """API key + API secret → daily access token (checksum flow)."""
    api_key = (api_key or "").strip().strip('"').strip("'").strip()
    api_secret = (api_secret or "").strip().strip('"').strip("'").strip()
    if not api_key or not api_secret:
        raise RuntimeError("Groww API key and secret are both required.")
    ts = str(int(time.time()))
    checksum = hashlib.sha256(f"{api_secret}{ts}".encode()).hexdigest()
    log.info("groww: minting token via checksum/secret")
    return _post_token(api_key, {"key_type": "approval", "checksum": checksum, "timestamp": ts})


def login(api_key: Optional[str] = None, totp_secret: Optional[str] = None,
          secret: Optional[str] = None) -> str:
    """Explicit login used by the app/login endpoint. Tries TOTP first, then
    checksum. Raises RuntimeError with the real error if both fail."""
    api_key = api_key or settings.groww_api_key
    totp_secret = totp_secret or settings.groww_totp_secret
    secret = secret or settings.groww_api_secret
    errors = []
    if api_key and totp_secret:
        try:
            return mint_via_totp(api_key, totp_secret)
        except Exception as e:
            errors.append(f"TOTP: {e}")
    if api_key and secret:
        try:
            return mint_via_secret(api_key, secret)
        except Exception as e:
            errors.append(f"checksum: {e}")
    raise RuntimeError("Groww login failed. " + (" | ".join(errors) if errors
                       else "Provide API key + TOTP secret (or API secret)."))


# ── resolution order used by GrowwClient ──────────────────────────────────────
def load_token() -> Optional[str]:
    # 1. Direct token from env (.env or Android-injected)
    env_tok = os.getenv("GROWW_ACCESS_TOKEN")
    if env_tok and env_tok.strip():
        tok = env_tok.strip()
        return tok[7:].strip() if tok.lower().startswith("bearer ") else tok

    # 2. Cached token (only if minted today)
    cached = _cached_token()
    if cached:
        return cached

    # 3. Mint a fresh one from TOTP / secret if creds are configured
    try:
        return login()
    except Exception as e:
        log.info(f"groww: no token and auto-mint failed — {e}")
        return None

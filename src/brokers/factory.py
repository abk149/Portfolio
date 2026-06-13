"""Active-broker factory. `get_broker()` returns the client for whichever
broker is selected by settings.broker (env BROKER). Either/or — exactly one
broker is active at a time.

Default is "upstox", so existing behaviour is byte-identical unless the user
opts into Groww.
"""
from __future__ import annotations

from typing import Optional

from config import settings
from src.utils.logger import get_logger

log = get_logger("brokers")


def get_broker(access_token: Optional[str] = None):
    """Return the active broker client (UpstoxClient or GrowwClient).
    Raises the broker's auth error if no token is available — same contract as
    the old UpstoxClient(), so existing try/except sites are unaffected."""
    b = settings.broker
    if b == "groww":
        from src.brokers.groww_client import GrowwClient
        return GrowwClient(access_token)
    from src.upstox.client import UpstoxClient
    return UpstoxClient(access_token)


def broker_status() -> dict:
    """Lightweight health check of the active broker for the dashboard."""
    b = settings.broker
    out = {"active": b, "ok": False, "user": None, "error": None}
    try:
        client = get_broker()
        prof = client.profile() or {}
        out["ok"] = True
        out["user"] = (prof.get("user_name") or prof.get("name")
                       or prof.get("email") or "(authenticated)")
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out

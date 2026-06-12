"""Persistent blacklist for instruments that consistently return no data.

We hit this for:
  - Delisted / suspended securities
  - Bonds, NCDs, mutual funds that slip past the equity filter
  - Tickers Upstox doesn't have history for

Blacklist entries auto-expire after `TTL_DAYS` so a delisted-then-relisted
stock will be retried.

File format: `.cache/instrument_blacklist.json`
{
  "RELIANCE.NS":       {"reason": "empty_candles", "ts": "2026-05-13T..."},
  "NSE_EQ|INE...":     {"reason": "error_HTTPError", "ts": "..."},
  "812REC27":          {"reason": "non_equity_pattern", "ts": "..."}
}
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from config import settings

TTL_DAYS = 30
_PATH: Path = settings.cache_dir / "instrument_blacklist.json"
_LOCK = threading.Lock()
_CACHE: dict[str, dict] | None = None


def _load() -> dict[str, dict]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not _PATH.exists():
        _CACHE = {}
        return _CACHE
    try:
        _CACHE = json.loads(_PATH.read_text())
    except Exception:
        _CACHE = {}
    # purge expired entries on load
    cutoff = datetime.now(timezone.utc) - timedelta(days=TTL_DAYS)
    _CACHE = {
        k: v for k, v in _CACHE.items()
        if _ts(v.get("ts")) > cutoff
    }
    return _CACHE


def _ts(s: Optional[str]) -> datetime:
    if not s:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _save() -> None:
    if _CACHE is None:
        return
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(_CACHE, indent=2, default=str))


def is_blacklisted(key: Optional[str]) -> bool:
    if not key:
        return False
    with _LOCK:
        return key in _load()


def mark_bad(key: Optional[str], reason: str = "") -> None:
    if not key:
        return
    with _LOCK:
        c = _load()
        c[key] = {"reason": reason,
                  "ts": datetime.now(timezone.utc).isoformat()}
        _save()


def unblacklist(key: str) -> bool:
    """Manually remove from the blacklist (e.g. after re-listing)."""
    with _LOCK:
        c = _load()
        if key in c:
            del c[key]
            _save()
            return True
    return False


def stats() -> dict:
    with _LOCK:
        c = _load()
        return {"total": len(c), "ttl_days": TTL_DAYS, "path": str(_PATH)}

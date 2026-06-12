"""Tiny pickle-on-disk cache keyed by namespace+key, with TTL."""
from __future__ import annotations

import pickle
import time
from pathlib import Path
from typing import Any, Callable

from config import settings


def _path(ns: str, key: str) -> Path:
    safe = key.replace("/", "_").replace("|", "_")
    p = settings.cache_dir / ns
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{safe}.pkl"


def _is_empty(val: Any) -> bool:
    """Don't cache empty results — they poison the next run if the upstream
    transient-failed (token expired, rate limited, etc.)."""
    if val is None:
        return True
    if isinstance(val, dict):
        return len(val) == 0
    # pandas DataFrame .empty without importing pandas here
    if hasattr(val, "empty"):
        try:
            return bool(val.empty)
        except Exception:
            return False
    if isinstance(val, (list, tuple, set)):
        return len(val) == 0
    return False


def get_or_set(ns: str, key: str, ttl_seconds: int, fn: Callable[[], Any]) -> Any:
    p = _path(ns, key)
    if p.exists() and (time.time() - p.stat().st_mtime) < ttl_seconds:
        cached = pickle.loads(p.read_bytes())
        if not _is_empty(cached):
            return cached
        # else: fall through and re-fetch
    val = fn()
    if not _is_empty(val):
        p.write_bytes(pickle.dumps(val))
    return val

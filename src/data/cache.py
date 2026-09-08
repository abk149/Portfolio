"""Tiny pickle-on-disk cache keyed by namespace+key, with TTL."""
from __future__ import annotations

import pickle
import time
from pathlib import Path
from typing import Any, Callable

from config import settings
from src.utils.logger import get_logger

log = get_logger("data.cache")


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
    """Read-through cache. An unreadable entry is a MISS, never an error.

    This used to call `pickle.loads` bare, which made any unreadable cache file
    a permanent poison pill: every call re-raised the same exception until
    somebody deleted the file by hand. That is a very good way to make a fixed
    bug look like it came back.

    It is not hypothetical. A DataFrame pickled by pandas 3.x carries a
    reference to pyarrow (its default string backend); loading that on the
    phone, which ships pandas 2.1.3 and no pyarrow, raises
    ModuleNotFoundError — and the Performance tab failed on every run until the
    cache was cleared. Library upgrades, moved classes and half-written files
    all fail the same way.

    So: any failure to read is treated as a miss, the bad file is removed, and
    we fetch fresh.
    """
    p = _path(ns, key)
    if p.exists() and (time.time() - p.stat().st_mtime) < ttl_seconds:
        try:
            cached = pickle.loads(p.read_bytes())
            if not _is_empty(cached):
                return cached
            # else: fall through and re-fetch
        except Exception as e:
            log.info(f"cache {ns}/{key} unreadable ({type(e).__name__}: {e}) "
                     f"— discarding and refetching")
            try:
                p.unlink()
            except Exception:
                pass

    val = fn()
    if not _is_empty(val):
        # An unwritable cache must not break the caller either — the value is
        # already computed; failing to store it is a performance problem, not a
        # correctness one.
        try:
            tmp = p.with_suffix(".pkl.tmp")
            tmp.write_bytes(pickle.dumps(val))
            tmp.replace(p)                    # atomic: never a half-written file
        except Exception as e:
            log.debug(f"cache {ns}/{key} not written: {e}")
    return val


def clear(ns: str | None = None) -> int:
    """Drop cached entries (a namespace, or everything). Returns files removed."""
    root = settings.cache_dir
    target = (root / ns) if ns else root
    removed = 0
    if not target.exists():
        return 0
    for f in target.rglob("*.pkl"):
        try:
            f.unlink()
            removed += 1
        except Exception:
            pass
    return removed

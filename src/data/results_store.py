"""Durable results for the long-running engines.

Macro Ideas, the DR-Quant funnel and the cash optimiser each take minutes and
cost network and LLM calls. Their output lived only in the in-memory job table,
so restarting the backend — which on a phone happens whenever Android reclaims
the process — threw all of it away and the screens came back blank.

Results are now written to disk as they're produced and restored on open, with
the age shown so a stale answer is never mistaken for a fresh one. Anything
older than `MAX_AGE_DAYS` is deleted rather than shown: a two-month-old macro
view is worse than no macro view, because it looks current.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from config import settings
from src.utils.logger import get_logger

log = get_logger("data.results")

MAX_AGE_DAYS = 60

# Friendly names for the UI's "as of" line.
KINDS = {
    "themes": "Macro Ideas",
    "quant": "DR-Quant funnel",
    "deploy_cash": "Cash allocation",
    "performance": "Performance analysis",
    "calendar": "Market calendar",
    "benchmark": "Index comparison",
}


def _dir():
    d = settings.cache_dir / "results"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(kind: str):
    safe = "".join(c for c in kind if c.isalnum() or c in "_-")
    return _dir() / f"{safe}.json"


def save(kind: str, payload: Any) -> None:
    """Persist an engine's result. Never let a storage problem lose the run."""
    if payload is None:
        return
    try:
        p = _path(kind)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(
            {"kind": kind,
             "saved_at": datetime.now().isoformat(timespec="seconds"),
             "payload": payload},
            default=str))
        tmp.replace(p)                     # atomic: never a half-written result
    except Exception as e:
        log.debug(f"could not persist {kind}: {e}")


def _age_days(saved_at: str) -> Optional[float]:
    try:
        return (datetime.now() - datetime.fromisoformat(saved_at)).total_seconds() / 86400
    except Exception:
        return None


def load(kind: str, max_age_days: int = MAX_AGE_DAYS) -> Optional[dict]:
    """Return ``{payload, saved_at, age_days, stale}`` or None.

    Too old is deleted, not returned. A two-month-old set of "current" ideas is
    actively misleading in a way that an empty screen is not.
    """
    p = _path(kind)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        log.info(f"stored {kind} result unreadable ({e}) — discarding")
        p.unlink(missing_ok=True)
        return None

    age = _age_days(data.get("saved_at", ""))
    if age is None or age > max_age_days:
        log.info(f"stored {kind} result is {age and round(age)}d old — deleting")
        p.unlink(missing_ok=True)
        return None
    return {
        "kind": kind,
        "label": KINDS.get(kind, kind),
        "payload": data.get("payload"),
        "saved_at": data.get("saved_at"),
        "age_days": round(age, 2),
        "age_label": _age_label(age),
    }


def _age_label(age_days: float) -> str:
    if age_days < 1 / 24:
        return "just now"
    if age_days < 1:
        h = int(age_days * 24)
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = int(age_days)
    return "yesterday" if d == 1 else f"{d} days ago"


def purge(max_age_days: int = MAX_AGE_DAYS) -> dict:
    """Delete everything past its shelf life. Safe to call often."""
    removed = []
    for p in _dir().glob("*.json"):
        try:
            age = _age_days(json.loads(p.read_text()).get("saved_at", ""))
            if age is None or age > max_age_days:
                p.unlink()
                removed.append(p.stem)
        except Exception:
            p.unlink(missing_ok=True)
            removed.append(p.stem)
    return {"removed": removed, "max_age_days": max_age_days}


def summary() -> dict:
    """What's stored and how old, for the UI's freshness indicators."""
    purge()
    out = []
    for kind, label in KINDS.items():
        got = load(kind)
        out.append({
            "kind": kind, "label": label,
            "present": got is not None,
            "saved_at": got["saved_at"] if got else None,
            "age_days": got["age_days"] if got else None,
            "age_label": got["age_label"] if got else None,
        })
    return {"results": out, "max_age_days": MAX_AGE_DAYS}

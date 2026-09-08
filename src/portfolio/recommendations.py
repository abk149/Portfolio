"""Every recommendation the system makes, captured so it can be scored.

The point of the ghost portfolio is to judge the ENGINES, not the user's own
instincts. That only works if the recommendations arrive on their own: if you
have to retype a symbol you liked the look of, you have quietly reintroduced
your own selection and the track record measures you again.

So each engine's output is recorded here the moment it is produced — macro
Ideas picks, DR-Quant validated names, and the optimiser's cash allocation —
with where it came from, what it said, and what it suggested paying. The ghost
tab then shows them as a queue with a Buy button, and because every ghost
position keeps its `source`, the P&L can be split by engine afterwards.

Recommendations are kept even after they're taken or dismissed. A record of
what the system suggested and what you did about it is the whole dataset.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import date, datetime
from typing import Optional

from config import settings
from src.utils.logger import get_logger

log = get_logger("portfolio.recommendations")

_LOCK = threading.Lock()

# Friendly names for the engines, used in the UI and in attribution.
SOURCES = {
    "macro-ideas": "Macro Ideas",
    "dr-quant": "DR-Quant funnel",
    "optimizer": "Cash optimiser",
    "deep-dive": "Deep dive",
    "manual": "Your own pick",
}


def _store_path():
    p = settings.cache_dir / "recommendations.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict:
    p = _store_path()
    if not p.exists():
        return {"items": []}
    try:
        return json.loads(p.read_text())
    except Exception as e:
        log.warning(f"recommendation store unreadable ({e}) — starting fresh")
        return {"items": []}


def _save(data: dict) -> None:
    p = _store_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str))
    tmp.replace(p)


def _key(source: str, symbol: str, run_id: Optional[str]) -> str:
    """Identity of a recommendation. Re-running an engine on the same day
    shouldn't pile up duplicates of the same call."""
    return f"{source}:{symbol}:{run_id or date.today().isoformat()}"


def record(items: list[dict], source: str, run_id: Optional[str] = None) -> dict:
    """Capture what an engine just recommended. Idempotent per run."""
    if not items:
        return {"added": 0, "source": source}

    with _LOCK:
        data = _load()
        existing = {i.get("key") for i in data["items"]}
        added = 0
        for it in items:
            sym = str(it.get("symbol") or "").upper().strip()
            if not sym:
                continue
            key = _key(source, sym, run_id)
            if key in existing:
                continue
            data["items"].append({
                "id": uuid.uuid4().hex[:10],
                "key": key,
                "symbol": sym,
                "source": source,
                "source_label": SOURCES.get(source, source),
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "run_id": run_id,
                "status": "pending",          # pending | taken | dismissed
                "rationale": (it.get("rationale") or "")[:600],
                "conviction": it.get("conviction"),
                "sector": it.get("sector"),
                "suggested_entry": it.get("suggested_entry"),
                "suggested_amount": it.get("suggested_amount"),
                "suggested_shares": it.get("suggested_shares"),
            })
            existing.add(key)
            added += 1
        if added:
            _save(data)
    log.info(f"recorded {added} recommendation(s) from {source}")
    return {"added": added, "source": source}


def list_all(status: Optional[str] = None) -> dict:
    data = _load()
    items = data.get("items", [])
    if status:
        items = [i for i in items if i.get("status") == status]
    items = sorted(items, key=lambda i: i.get("created_at", ""), reverse=True)
    counts: dict[str, int] = {}
    for i in data.get("items", []):
        counts[i.get("status", "pending")] = counts.get(i.get("status", "pending"), 0) + 1
    return {"items": items, "counts": counts,
            "sources": sorted({i.get("source") for i in data.get("items", [])})}


def set_status(rec_id: str, status: str, ghost_id: Optional[str] = None) -> dict:
    with _LOCK:
        data = _load()
        for i in data["items"]:
            if i["id"] == rec_id:
                i["status"] = status
                i["acted_at"] = datetime.now().isoformat(timespec="seconds")
                if ghost_id:
                    i["ghost_position_id"] = ghost_id
                _save(data)
                return {"ok": True, "item": i}
    return {"error": "Recommendation not found."}


def clear(which: str = "dismissed") -> dict:
    """Drop dismissed items, or everything."""
    with _LOCK:
        data = _load()
        before = len(data["items"])
        if which == "all":
            data["items"] = []
        else:
            data["items"] = [i for i in data["items"] if i.get("status") != which]
        _save(data)
    return {"ok": True, "removed": before - len(data["items"])}


# ---------------------------------------------------------------------------
# Scoring the engines
# ---------------------------------------------------------------------------
def attribution(ghost_snapshot: dict) -> dict:
    """Split the ghost book's P&L by which engine suggested each position.

    This is the number the whole exercise exists to produce: not "how is my
    paper portfolio doing" but "which of these engines is worth listening to".
    """
    rows: dict[str, dict] = {}

    def _bucket(src: str) -> dict:
        return rows.setdefault(src, {
            "source": src, "label": SOURCES.get(src, src),
            "n_open": 0, "n_closed": 0, "invested": 0.0, "value": 0.0,
            "unrealised": 0.0, "realised": 0.0, "wins": 0, "losses": 0,
        })

    for p in (ghost_snapshot.get("open") or []):
        b = _bucket(p.get("source") or "manual")
        b["n_open"] += 1
        b["invested"] += float(p.get("invested") or 0)
        b["value"] += float(p.get("value") or 0)
        b["unrealised"] += float(p.get("pnl") or 0)
    for p in (ghost_snapshot.get("closed") or []):
        b = _bucket(p.get("source") or "manual")
        b["n_closed"] += 1
        pnl = float(p.get("pnl") or 0)
        b["realised"] += pnl
        b["wins" if pnl > 0 else "losses"] += 1

    out = []
    for b in rows.values():
        total = b["unrealised"] + b["realised"]
        decided = b["wins"] + b["losses"]
        out.append({
            **{k: round(v, 2) if isinstance(v, float) else v for k, v in b.items()},
            "total_pnl": round(total, 2),
            "return_pct": round(total / b["invested"] * 100, 2) if b["invested"] else None,
            "hit_rate_pct": round(b["wins"] / decided * 100, 1) if decided else None,
            "n_total": b["n_open"] + b["n_closed"],
        })
    out.sort(key=lambda r: -(r["total_pnl"] or 0))
    return {
        "by_source": out,
        "note": ("Positions are attributed to the engine that suggested them. "
                 "Hit rate counts only CLOSED positions — an open loser hasn't "
                 "lost yet, and counting it would flatter whichever engine you "
                 "happened not to sell."),
    }

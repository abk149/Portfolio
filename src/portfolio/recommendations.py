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


def list_all(status: Optional[str] = None, max_age_days: int = 60) -> dict:
    prune(max_age_days)
    data = _load()
    items = data.get("items", [])
    now = datetime.now()
    for i in items:
        try:
            age = (now - datetime.fromisoformat(i["created_at"])).total_seconds() / 86400
            i["age_days"] = round(age, 2)
            i["age_label"] = ("just now" if age < 1 / 24 else
                              f"{int(age * 24)}h ago" if age < 1 else
                              "yesterday" if int(age) == 1 else f"{int(age)} days ago")
        except Exception:
            i["age_days"] = None
            i["age_label"] = None
    if status:
        items = [i for i in items if i.get("status") == status]
    items = sorted(items, key=lambda i: i.get("created_at", ""), reverse=True)
    counts: dict[str, int] = {}
    for i in data.get("items", []):
        counts[i.get("status", "pending")] = counts.get(i.get("status", "pending"), 0) + 1
    return {"items": items, "counts": counts,
            "sources": sorted({i.get("source") for i in data.get("items", [])})}


def apply_sizing(sized: dict, cash: float, max_weight: float) -> dict:
    """Write portfolio-aware position sizes back onto the pending queue.

    Engines size their own ideas inconsistently — the cash optimiser thinks in
    weights of your book, Macro Ideas and DR-Quant don't size at all, so the UI
    fell back to a flat default. Running the whole queue through the optimiser
    against your real holdings gives every recommendation the same currency:
    an amount, a share count, and what it would be as a share of the book.

    A symbol the optimiser funds at zero keeps a note saying so. That is a real
    answer — "good idea, but not alongside what you already own" — and it would
    be lost if we silently left the old flat default in place.
    """
    stamp = datetime.now().isoformat(timespec="seconds")
    with _LOCK:
        data = _load()
        touched = 0
        for i in data["items"]:
            if i.get("status") != "pending":
                continue
            fit = sized.get(i["symbol"])
            if fit is None:
                continue
            i["suggested_amount"] = fit.get("amount")
            i["suggested_shares"] = fit.get("shares")
            i["suggested_weight_pct"] = fit.get("weight_pct")
            i["suggested_entry"] = fit.get("price") or i.get("suggested_entry")
            i["sizing_note"] = fit.get("note")
            i["sized_at"] = stamp
            i["sized_for_cash"] = cash
            i["sized_max_weight"] = max_weight
            touched += 1
        if touched:
            _save(data)
    return {"ok": True, "sized": touched, "cash": cash}


def prune(max_age_days: int = 60) -> dict:
    """Drop recommendations past their shelf life.

    A two-month-old "buy this now" is not a recommendation, it's a fossil.
    """
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(days=max_age_days)
    with _LOCK:
        data = _load()
        before = len(data["items"])
        kept = []
        for i in data["items"]:
            try:
                if datetime.fromisoformat(i["created_at"]) >= cutoff:
                    kept.append(i)
            except Exception:
                kept.append(i)              # unparseable date — keep it
        data["items"] = kept
        if len(kept) != before:
            _save(data)
    return {"removed": before - len(kept), "max_age_days": max_age_days}


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

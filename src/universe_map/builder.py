"""Universe map — the data ingestion engine.

This is NOT a one-off scan. It's the crawler that fills your Knowledge Base:

  Stage A — technical scan over EVERY stock in the universe (Upstox candles)
  Stage B — fundamentals for EVERY technical-valid stock, multi-source:
              NSE India → Yahoo → screener.in
  Stage C — write each stock into the KB `universe` collection

Incremental by design: a stock whose KB entry is younger than `max_age_days`
is skipped (we reuse the stored data). So the first build is long, every
build after that only refreshes stale names. Run it daily/weekly via the
scheduler and the KB stays current.

The D-R1-Quant funnel then reads fundamentals straight from the KB instead
of re-fetching live.

Output JSON cache: .cache/universe_map/<universe>.json (for the dashboard plot)
Persistent store:  ChromaDB `universe` collection (for the funnel + search)
"""
from __future__ import annotations

import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from config import settings
from src.screener.engine import ScreenerEngine
from src.screener.fundamental import fundamental_score
from src.utils.logger import get_logger

log = get_logger("universe_map")


def _map_dir() -> Path:
    d = settings.cache_dir / "universe_map"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path(universe: str) -> Path:
    return _map_dir() / f"{universe}.json"


def _fail(universe: str, msg: str) -> dict:
    """Report a build failure WITHOUT destroying an existing map.

    These abort paths used to write an empty result straight over the cache, so
    a transient auth blip wiped a map that took an hour to crawl. Now the
    existing map is left alone and simply carries the error alongside it.
    """
    err = {"universe": universe, "built_at": datetime.utcnow().isoformat() + "Z",
           "count": 0, "tech_total": 0, "fund_scanned": 0, "fund_reused": 0,
           "stocks": [], "error": msg}
    try:
        existing = load_cached(universe)
    except Exception:
        existing = None
    if existing and existing.get("stocks"):
        log.warning(f"universe map build failed ({msg}) — keeping the "
                    f"{len(existing['stocks'])} stocks already cached.")
        return {**existing, "error": msg, "stale": True}
    _write_cache(universe, err)
    return err


def _write_cache(universe: str, payload: dict) -> None:
    """Atomic cache write — a half-written JSON file would poison every later
    read, and checkpointing means we now write mid-crawl."""
    path = _cache_path(universe)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, default=str))
    tmp.replace(path)


def load_cached(universe: str) -> Optional[dict]:
    p = _cache_path(universe)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _scrub(v):
    """NaN / ±Inf / numpy types → JSON-safe."""
    import numpy as np
    if v is None:
        return None
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(v, dict):
        return {k: _scrub(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_scrub(x) for x in v]
    return v


def _upstox_health_check() -> tuple[bool, str]:
    try:
        from src.brokers import get_broker
        prof = get_broker().profile()
        return True, prof.get("user_name") or prof.get("email") or "(unknown)"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _adapt_fundamentals(symbol: str, raw: dict) -> dict:
    """Merged-source raw dict → fundamental_score() schema + score."""
    adapted = {
        "trailingPE": raw.get("pe"),
        "priceToBook": None,
        "returnOnEquity": (raw["roe_pct"] / 100) if raw.get("roe_pct") is not None else None,
        "debtToEquity": raw.get("debt_to_equity"),
        "earningsGrowth": (raw["profit_growth_pct"] / 100) if raw.get("profit_growth_pct") is not None else None,
        "revenueGrowth": (raw["sales_growth_pct"] / 100) if raw.get("sales_growth_pct") is not None else None,
        "profitMargins": None,
        "freeCashflow": None,
        "marketCap": (raw["market_cap_cr"] * 1e7) if raw.get("market_cap_cr") else None,
        "sector": raw.get("sector"),
        "industry": raw.get("industry"),
    }
    fs = fundamental_score(adapted)
    return {
        "fund_score": fs.get("score"),
        "PE": adapted["trailingPE"],
        "ROE": adapted["returnOnEquity"],
        "DE": adapted["debtToEquity"],
        "sales_growth_pct": raw.get("sales_growth_pct"),
        "profit_growth_pct": raw.get("profit_growth_pct"),
        "sector": adapted["sector"],
        "industry": adapted["industry"],
        "market_cap_cr": raw.get("market_cap_cr"),
        "fund_sources": raw.get("_sources", []),
    }


def build_universe_map(
    universe: str = "all_nse",
    max_age_days: float = 7.0,
    workers: int = 4,
    progress: Optional[Callable[[dict], None]] = None,
    checkpoint_every: int = 25,
) -> dict:
    """Crawl the whole universe, score it, write it into the KB.

    `max_age_days`: a stock whose KB entry is younger than this is NOT
    re-fetched — its stored data is reused. Set to 0 to force a full refresh.

    `progress`: called with {stage, done, total, fetched, reused, pct, message}
    as the crawl advances, so a UI can show a live bar instead of a spinner.

    `checkpoint_every`: the partial map is flushed to the JSON cache every N
    stocks. The first full-universe build takes long enough that it WILL
    sometimes be interrupted (app backgrounded, device sleeps, process killed);
    without checkpoints that lost every row, because the cache was only written
    after the last stock. With them, an interrupted run leaves a usable —
    explicitly `partial` — map, and the next run reuses it.
    """
    print(f"[UMAP] universe map build: universe={universe} "
          f"max_age_days={max_age_days}", flush=True, file=sys.stderr)

    ok, who = _upstox_health_check()
    if not ok:
        msg = (f"Upstox not reachable ({who}). Token likely expired — "
               f"re-auth via dashboard ⚙ Settings → 🔐 Upstox login.")
        print(f"[UMAP] ✗ ABORT — {msg}", flush=True, file=sys.stderr)
        return _fail(universe, msg)
    print(f"[UMAP] ✓ Upstox auth OK ({who})", flush=True, file=sys.stderr)

    def _emit(stage: str, done: int = 0, total: int = 0, fetched: int = 0,
              reused: int = 0, message: str = "") -> None:
        """Best-effort progress ping — never let a reporting bug kill a crawl."""
        if not progress:
            return
        try:
            progress({
                "stage": stage, "done": done, "total": total,
                "fetched": fetched, "reused": reused,
                "pct": round(done / total * 100, 1) if total else 0.0,
                "message": message,
            })
        except Exception as e:
            log.debug(f"progress callback failed: {e}")

    _emit("technical", message="Scanning the universe for price data…")

    # ── Stage A: technical scan over the WHOLE universe ──
    eng = ScreenerEngine(workers=workers)
    print("[UMAP] Stage A — technical scan over entire universe …",
          flush=True, file=sys.stderr)
    tech = eng.technical_scan(universe, tech_min=0.0)   # 0 = keep everything scored
    if tech.empty:
        msg = ("Technical scan returned 0 rows — every Upstox daily call came "
               "back empty. Token expired mid-run, or instrument keys stale.")
        print(f"[UMAP] ✗ {msg}", flush=True, file=sys.stderr)
        return _fail(universe, msg)
    print(f"[UMAP] Stage A done — {len(tech)} stocks technically scored",
          flush=True, file=sys.stderr)
    _emit("fundamentals", 0, len(tech),
          message=f"{len(tech)} stocks scored — now fetching fundamentals")

    # ── Stage B+C: fundamentals for EVERY stock + KB write, incremental ──
    from src.kb import KnowledgeBase
    kb = KnowledgeBase.get()
    from src.tools.screener_in import fetch_fundamentals

    rows = tech.to_dict("records")
    total = len(rows)
    print(f"[UMAP] Stage B — fundamentals for all {total} stocks "
          f"(skipping KB entries < {max_age_days}d old) …",
          flush=True, file=sys.stderr)

    n_reused = 0
    n_fetched = 0
    out_records: list[dict] = []

    def _snapshot(partial: bool) -> dict:
        return {
            "universe": universe,
            "built_at": datetime.utcnow().isoformat() + "Z",
            "count": len(out_records),
            "tech_total": len(tech),
            "fund_scanned": n_fetched,
            "fund_reused": n_reused,
            "partial": partial,
            "expected_total": total,
            "stocks": [_scrub(r) for r in out_records],
        }

    def _checkpoint(done: int) -> None:
        """Flush what we have so far. Cheap relative to a network fetch per
        stock, and it is the difference between an interrupted build being a
        setback and being a total loss."""
        try:
            _write_cache(universe, _snapshot(partial=True))
        except Exception as e:
            log.debug(f"checkpoint write failed at {done}: {e}")

    def _process(row: dict) -> dict:
        nonlocal n_reused, n_fetched
        sym = row["symbol"]
        fund_data_for_docs = None

        # Incremental: reuse fresh KB data
        try:
            age = kb.stock_age_days(sym)
            if age is not None and age < max_age_days:
                stored = kb.get_stock(sym) or {}
                merged = {**row}
                for k in ("fund_score", "PE", "ROE", "DE", "sector", "industry",
                        "combined", "recommendation", "market_cap_cr"):
                    if stored.get(k) not in (None, ""):
                        merged[k] = stored[k]
                n_reused += 1
                # The full raw dict is often stored in 'data' column
                fund_data_for_docs = stored.get("_data")
            else:
                # Fresh fetch
                from src.tools.screener_in import fetch_fundamentals
                raw = fetch_fundamentals(sym) or {}
                fund = _adapt_fundamentals(sym, raw)
                srcs = raw.get("_sources", [])
                log.info(
                    f"  {sym}: sources={srcs or 'NONE'} "
                    f"PE={fund.get('PE')} ROE={fund.get('ROE')} "
                    f"D/E={fund.get('DE')} fund_score={fund.get('fund_score')}"
                )
                n_fetched += 1
                merged = {**row, **fund}
                fund_data_for_docs = raw
        except Exception as e:
            log.info(f"  {sym}: fund fetch FAILED — {type(e).__name__}: {e}")
            merged = {**row, "fund_score": None}
            fund_data_for_docs = None

        # Combined score + recommendation
        t, f = merged.get("tech_score"), merged.get("fund_score")
        if t is not None and f:
            merged["combined"] = round(0.6 * t + 0.4 * f, 1)
            c = merged["combined"]
            merged["recommendation"] = (
                "STRONG_BUY" if c >= 70 else "BUY" if c >= 55 else
                "HOLD" if c >= 40 else "AVOID" if c >= 25 else "SELL")
        else:
            merged["combined"] = None
            merged["recommendation"] = (
                "TECH_BUY" if (t or 0) >= 70 else
                "TECH_WATCH" if (t or 0) >= 55 else "AVOID")

        # Persist into the KB
        try:
            kb.upsert_stock(sym, merged)

            # ── Stage D: Gather Documents for Knowledge Base ──
            # Only for promising candidates (Score >= 55) to avoid slow build
            # Cap to 1 document per stock during full-universe scan
            score = merged.get("combined") or merged.get("tech_score") or 0
            if score >= 55:
                try:
                    from src.tools.document_fetcher import fetch_documents_multisource
                    # Check if we already have documents for this symbol in KB to avoid re-ingesting
                    has_docs = any(sym in (d.get("title") or "") for d in kb.documents())
                    if not has_docs:
                        log.info(f"  [KB] Gathering documents for {sym} (score={score})...")
                        fetch_documents_multisource(sym, fundamentals=fund_data_for_docs, max_docs=1, ingest_kb=True)
                except Exception as de:
                    log.debug(f"  [KB] Document gather failed for {sym}: {de}")
        except Exception as e:
            log.warning(f"KB upsert failed for {sym}: {e}")

        return merged

    # Android check: use simple loop for stability
    import os
    if os.getenv("APP_FILES_DIR"):
        print("[UMAP] Android detected — using sequential processing for stability",
              flush=True, file=sys.stderr)
        done = 0
        for r in rows:
            try:
                out_records.append(_process(r))
            except Exception as e:
                # One bad symbol must never abort a 2000-stock crawl.
                log.debug(f"process error for {r.get('symbol')}: {e}")
            done += 1
            if done % checkpoint_every == 0:
                _checkpoint(done)
            if done % 10 == 0 or done == total:
                _emit("fundamentals", done, total, n_fetched, n_reused,
                      f"{done} of {total} stocks")
            if done % 20 == 0 or done == total:
                print(f"[UMAP] {done}/{total} processed "
                      f"({n_fetched} fetched, {n_reused} reused)",
                      flush=True, file=sys.stderr)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_process, r): r["symbol"] for r in rows}
            done = 0
            for fut in as_completed(futs):
                done += 1
                try:
                    out_records.append(fut.result())
                except Exception as e:
                    log.debug(f"process error: {e}")
                if done % checkpoint_every == 0:
                    _checkpoint(done)
                if done % 10 == 0 or done == total:
                    _emit("fundamentals", done, total, n_fetched, n_reused,
                          f"{done} of {total} stocks")
                if done % 50 == 0 or done == total:
                    print(f"[UMAP] {done}/{total} processed "
                        f"({n_fetched} fetched, {n_reused} reused from KB)",
                        flush=True, file=sys.stderr)

    result = _snapshot(partial=False)
    records = result["stocks"]
    _write_cache(universe, result)
    _emit("done", total, total, n_fetched, n_reused,
          f"Done — {len(records)} stocks mapped")
    print(f"[UMAP] ✓ done — {len(records)} stocks "
          f"({n_fetched} freshly fetched, {n_reused} reused) → "
          f"KB now holds {kb.universe.count()} stocks",
          flush=True, file=sys.stderr)
    return result

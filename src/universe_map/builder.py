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
from typing import Optional

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
        from src.upstox.client import UpstoxClient
        prof = UpstoxClient().profile()
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
) -> dict:
    """Crawl the whole universe, score it, write it into the KB.

    `max_age_days`: a stock whose KB entry is younger than this is NOT
    re-fetched — its stored data is reused. Set to 0 to force a full refresh.
    """
    print(f"[UMAP] universe map build: universe={universe} "
          f"max_age_days={max_age_days}", flush=True, file=sys.stderr)

    ok, who = _upstox_health_check()
    if not ok:
        msg = (f"Upstox not reachable ({who}). Token likely expired — "
               f"re-auth via dashboard ⚙ Settings → 🔐 Upstox login.")
        print(f"[UMAP] ✗ ABORT — {msg}", flush=True, file=sys.stderr)
        empty = {"universe": universe, "built_at": datetime.utcnow().isoformat() + "Z",
                 "count": 0, "tech_total": 0, "fund_scanned": 0, "fund_reused": 0,
                 "stocks": [], "error": msg}
        _cache_path(universe).write_text(json.dumps(empty))
        return empty
    print(f"[UMAP] ✓ Upstox auth OK ({who})", flush=True, file=sys.stderr)

    # ── Stage A: technical scan over the WHOLE universe ──
    eng = ScreenerEngine(workers=workers)
    print("[UMAP] Stage A — technical scan over entire universe …",
          flush=True, file=sys.stderr)
    tech = eng.technical_scan(universe, tech_min=0.0)   # 0 = keep everything scored
    if tech.empty:
        msg = ("Technical scan returned 0 rows — every Upstox daily call came "
               "back empty. Token expired mid-run, or instrument keys stale.")
        print(f"[UMAP] ✗ {msg}", flush=True, file=sys.stderr)
        result = {"universe": universe, "built_at": datetime.utcnow().isoformat() + "Z",
                  "count": 0, "tech_total": 0, "fund_scanned": 0, "fund_reused": 0,
                  "stocks": [], "error": msg}
        _cache_path(universe).write_text(json.dumps(result))
        return result
    print(f"[UMAP] Stage A done — {len(tech)} stocks technically scored",
          flush=True, file=sys.stderr)

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

    def _process(row: dict) -> dict:
        nonlocal n_reused, n_fetched
        sym = row["symbol"]

        # Incremental: reuse fresh KB data
        age = kb.stock_age_days(sym)
        if age is not None and age < max_age_days:
            stored = kb.get_stock(sym) or {}
            merged = {**row}
            for k in ("fund_score", "PE", "ROE", "DE", "sector", "industry",
                      "combined", "recommendation", "market_cap_cr"):
                if stored.get(k) not in (None, ""):
                    merged[k] = stored[k]
            n_reused += 1
            return merged

        # Fresh fetch
        try:
            raw = fetch_fundamentals(sym) or {}
            fund = _adapt_fundamentals(sym, raw)
            srcs = raw.get("_sources", [])
            log.info(
                f"  {sym}: sources={srcs or 'NONE'} "
                f"PE={fund.get('PE')} ROE={fund.get('ROE')} "
                f"D/E={fund.get('DE')} fund_score={fund.get('fund_score')}"
            )
        except Exception as e:
            log.info(f"  {sym}: fund fetch FAILED — {type(e).__name__}: {e}")
            fund = {"fund_score": None}
        n_fetched += 1
        merged = {**row, **fund}

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
        kb.upsert_stock(sym, merged)
        return merged

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_process, r): r["symbol"] for r in rows}
        done = 0
        for fut in as_completed(futs):
            done += 1
            try:
                out_records.append(fut.result())
            except Exception as e:
                log.debug(f"process error: {e}")
            if done % 50 == 0 or done == total:
                print(f"[UMAP] {done}/{total} processed "
                      f"({n_fetched} fetched, {n_reused} reused from KB)",
                      flush=True, file=sys.stderr)

    records = [_scrub(r) for r in out_records]
    result = {
        "universe": universe,
        "built_at": datetime.utcnow().isoformat() + "Z",
        "count": len(records),
        "tech_total": len(tech),
        "fund_scanned": n_fetched,
        "fund_reused": n_reused,
        "stocks": records,
    }
    _cache_path(universe).write_text(json.dumps(result, default=str))
    print(f"[UMAP] ✓ done — {len(records)} stocks "
          f"({n_fetched} freshly fetched, {n_reused} reused) → "
          f"KB now holds {kb.universe.count()} stocks",
          flush=True, file=sys.stderr)
    return result

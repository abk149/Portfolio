"""Optional TimescaleDB connector + JSONL fallback.

If TIMESCALE_DSN is set we write to Postgres/Timescale; otherwise we append to
JSONL files under `.cache/db/` so the rest of the system still works on a
laptop with no DB installed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import settings
from src.utils.logger import get_logger

log = get_logger("db")


class TimescaleDB:
    def __init__(self, dsn: Optional[str] = None):
        self.dsn = dsn or settings.timescale_dsn
        self.conn = None
        self.fallback_dir = settings.cache_dir / "db"
        self.fallback_dir.mkdir(parents=True, exist_ok=True)
        if self.dsn:
            try:
                import psycopg2
                self.conn = psycopg2.connect(self.dsn)
                self.conn.autocommit = True
                log.info("Connected to TimescaleDB.")
            except Exception as e:
                log.warning(f"TimescaleDB unavailable, falling back to JSONL: {e}")
                self.conn = None

    @property
    def live(self) -> bool:
        return self.conn is not None

    def _jsonl(self, table: str, row: dict) -> None:
        p = self.fallback_dir / f"{table}.jsonl"
        with p.open("a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")

    def exec(self, sql: str, params: tuple = ()) -> None:
        if not self.live:
            return
        with self.conn.cursor() as cur:
            cur.execute(sql, params)

    # ---------- writers ----------
    def log_agent(self, run_id: str, stage: str, actor: str, action: str,
                  symbol: Optional[str], payload: Any) -> None:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id, "stage": stage, "actor": actor,
            "action": action, "symbol": symbol,
            "payload": payload,
        }
        if self.live:
            self.exec(
                "INSERT INTO agent_logs (run_id, stage, actor, action, symbol, payload) "
                "VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
                (run_id, stage, actor, action, symbol, json.dumps(payload, default=str)),
            )
        self._jsonl("agent_logs", row)

    def upsert_fundamentals(self, symbol: str, data: dict, source: str = "") -> None:
        if self.live:
            self.exec(
                """INSERT INTO stock_fundamentals
                (symbol, debt_to_equity, current_ratio, free_cash_flow_cr,
                 promoter_pledging_pct, revenue_growth_pct, earnings_growth_pct,
                 pe, roe, source, notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (symbol, data.get("debt_to_equity"), data.get("current_ratio"),
                 data.get("free_cash_flow_cr"), data.get("promoter_pledging_pct"),
                 data.get("revenue_growth_pct"), data.get("earnings_growth_pct"),
                 data.get("pe"), data.get("roe"), source, data.get("notes")),
            )
        self._jsonl("stock_fundamentals", {"symbol": symbol, "source": source, **data})

    def log_macro(self, m: dict) -> None:
        if self.live:
            self.exec(
                """INSERT INTO macro_snapshots (india_vix, nifty_pcr, usdinr,
                                                nifty_chg_pct, mode)
                   VALUES (%s,%s,%s,%s,%s)""",
                (m.get("india_vix"), m.get("nifty_pcr"), m.get("usdinr"),
                 m.get("nifty_change_pct"), m.get("mode")),
            )
        self._jsonl("macro_snapshots", m)


_db: Optional[TimescaleDB] = None


def get_db() -> TimescaleDB:
    global _db
    if _db is None:
        _db = TimescaleDB()
    return _db


def agent_log(run_id: str, stage: str, actor: str, action: str,
              symbol: Optional[str] = None, **payload) -> None:
    get_db().log_agent(run_id, stage, actor, action, symbol, payload)

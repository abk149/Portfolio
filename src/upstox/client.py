"""Thin wrapper over the Upstox v2 REST API.

Covers the endpoints we actually use: profile, funds, holdings, positions,
order book, trades, historical candles, intraday candles, market quote, LTP.

Docs: https://upstox.com/developer/api-documentation/
"""
from __future__ import annotations

import threading
import time
from datetime import date, timedelta
from typing import Any, Optional

import pandas as pd
import requests

from config import settings
from src.upstox.auth import load_token
from src.utils.logger import get_logger

log = get_logger("upstox.client")


class _RateLimiter:
    """Process-wide ADAPTIVE throttle for the Upstox API.

    Upstox quota-limits aggressively — a sustained 3000-stock scan trips a
    429 with a multi-minute `Retry-After`. Honouring that literally froze the
    whole scan. Instead we adapt: every 429 slows the steady rate down,
    every success gently speeds it back up. Worst case we crawl at ~0.5 req/s
    but the scan never deadlocks; instruments that don't get served this run
    are simply picked up on the next incremental run.
    """

    def __init__(self, max_per_sec: float = 5.0):
        self._base = 1.0 / max_per_sec
        self._floor = 1.0 / 0.5            # never slower than 1 req / 2s
        self.min_interval = self._base
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            gap = now - self._last
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            self._last = time.monotonic()

    def penalize(self) -> None:
        """Called on a 429 — back the steady rate off."""
        with self._lock:
            self.min_interval = min(self.min_interval * 1.6, self._floor)

    def relax(self) -> None:
        """Called on a clean response — drift back toward the base rate."""
        with self._lock:
            self.min_interval = max(self.min_interval * 0.97, self._base)


# Shared across all UpstoxClient instances / threads in this process.
_UPSTOX_LIMITER = _RateLimiter(max_per_sec=5.0)


class UpstoxAuthError(RuntimeError):
    pass


class UpstoxClient:
    """Lightweight authenticated client. Lazy-loads the cached token."""

    def __init__(self, access_token: Optional[str] = None):
        self.base = settings.upstox_base_url
        self._token = access_token or self._token_from_cache()

    @staticmethod
    def _token_from_cache() -> str:
        t = load_token()
        if not t or "access_token" not in t:
            raise UpstoxAuthError(
                "No cached Upstox token. Run: python -m src.upstox.auth"
            )
        return t["access_token"]

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Api-Version": "2.0",
        }

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        _UPSTOX_LIMITER.wait()      # process-wide adaptive throttle
        url = f"{self.base.rstrip('/')}{path}"
        r = requests.get(url, headers=self._headers,
                         params=params, timeout=30)
        if r.status_code == 401:
            raise UpstoxAuthError(f"Upstox token expired or invalid (401). Server responded: {r.text}")

        if r.status_code == 429:
            # Quota hit. Slow the steady rate down, take ONE short nap
            # (capped — we IGNORE Upstox's multi-minute Retry-After, that
            # would deadlock a bulk scan), retry once, then give up: return
            # None so the caller treats it as "no data this run".
            _UPSTOX_LIMITER.penalize()
            nap = min(float(r.headers.get("Retry-After", 3) or 3), 8.0)
            time.sleep(nap)
            _UPSTOX_LIMITER.wait()
            r = requests.get(f"{self.base}{path}", headers=self._headers,
                             params=params, timeout=30)
            if r.status_code == 429:
                log.debug(f"Upstox still 429 on {path} — skipping (next run will retry)")
                return None
            if r.status_code == 401:
                raise UpstoxAuthError("Upstox token expired. Re-run python -m src.upstox.auth")

        if r.status_code != 200:
            # Log the URL + body so 405/400 (e.g. out-of-window date ranges on
            # /charges/historical-trades) are diagnosable from the Terminal.
            log.info(f"Upstox {r.status_code} {url} params={params} → {r.text[:200]}")
            return None

        _UPSTOX_LIMITER.relax()      # clean response — drift back toward base rate
        return r.json().get("data")

    # ---------------- account ----------------
    def profile(self) -> dict:
        return self._get("/user/profile")

    def funds(self) -> dict:
        return self._get("/user/get-funds-and-margin")

    # ---------------- portfolio ----------------
    def holdings(self) -> list[dict]:
        return self._get("/portfolio/long-term-holdings") or []

    def positions(self) -> list[dict]:
        return self._get("/portfolio/short-term-positions") or []

    # ---------------- orders / trades ----------------
    def order_book(self) -> list[dict]:
        return self._get("/order/retrieve-all") or []

    def trades_today(self) -> list[dict]:
        return self._get("/order/trades/get-trades-for-day") or []

    def trade_history(self, start: date, end: date, segment: str = "EQ") -> list[dict]:
        """Historical trades. Upstox limits to 1 financial year per call."""
        return self._get(
            "/charges/historical-trades",
            params={
                "segment": segment,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "page_number": 1,
                "page_size": 100,
            },
        ) or []

    # ---------------- market data ----------------
    def ltp(self, instruments: list[str]) -> dict:
        """instruments: list of instrument keys like 'NSE_EQ|INE002A01018'."""
        return self._get("/market-quote/ltp", params={"instrument_key": ",".join(instruments)}) or {}

    def quote(self, instruments: list[str]) -> dict:
        return self._get("/market-quote/quotes", params={"instrument_key": ",".join(instruments)}) or {}

    def candles(
        self,
        instrument_key: str,
        interval: str = "day",  # 1minute | 30minute | day | week | month
        to_date: Optional[date] = None,
        from_date: Optional[date] = None,
    ) -> pd.DataFrame:
        to_date = to_date or date.today()
        from_date = from_date or (to_date - timedelta(days=365))
        path = f"/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}"
        data = self._get(path)
        rows = (data or {}).get("candles", [])
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "oi"])
        if not df.empty:
            df["ts"] = pd.to_datetime(df["ts"])
            df = df.sort_values("ts").set_index("ts")
        return df

    def intraday_candles(self, instrument_key: str, interval: str = "30minute") -> pd.DataFrame:
        path = f"/historical-candle/intraday/{instrument_key}/{interval}"
        data = self._get(path)
        rows = (data or {}).get("candles", [])
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "oi"])
        if not df.empty:
            df["ts"] = pd.to_datetime(df["ts"])
            df = df.sort_values("ts").set_index("ts")
        return df

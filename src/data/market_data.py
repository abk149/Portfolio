"""Unified market data — Upstox only. yfinance has been removed.

Upstox v2 endpoints we use (docs: https://upstox.com/developer/api-documentation):

  • Historical daily/weekly/monthly OHLCV
      GET /v2/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}
      interval ∈ {1minute, 30minute, day, week, month}

  • Intraday OHLCV (today only)
      GET /v2/historical-candle/intraday/{instrument_key}/{interval}
      interval ∈ {1minute, 30minute}

  • LTP / quotes (snapshots)
      GET /v2/market-quote/ltp?instrument_key=KEY1,KEY2,…
      GET /v2/market-quote/quotes?instrument_key=KEY

Auto-resolution: if a caller passes a yf_ticker / trading symbol but no
instrument_key, we look it up via the Upstox instrument master.

Tickers that fail to return any data are blacklisted (see
src/data/instrument_blacklist.py) so subsequent runs skip them.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pandas as pd

from src.data.cache import get_or_set
from src.data.instrument_blacklist import is_blacklisted, mark_bad
from src.upstox.client import UpstoxClient
from src.brokers import get_broker
from src.utils.logger import get_logger

log = get_logger("data")


class MarketData:
    def __init__(self, upstox: Optional[UpstoxClient] = None):
        if upstox is not None:
            self.upstox = upstox
        else:
            try:
                self.upstox = get_broker()
            except Exception as e:
                log.warning(f"Upstox client unavailable: {e}")
                self.upstox = None

    # ---------- helpers ----------
    def _resolve_key(self, instrument_key: Optional[str],
                     yf_ticker: Optional[str]) -> Optional[str]:
        if instrument_key:
            return instrument_key
        if not yf_ticker:
            return None
        try:
            from src.data.instruments import resolve_instrument_key
            return resolve_instrument_key(yf_ticker)
        except Exception as e:
            log.debug(f"instrument resolve failed for {yf_ticker}: {e}")
            return None

    # ---------- daily OHLCV ----------
    def daily(self, yf_ticker: str, instrument_key: Optional[str],
              lookback_days: int = 365) -> pd.DataFrame:
        ikey = self._resolve_key(instrument_key, yf_ticker)
        # Skip known-bad instruments
        if is_blacklisted(ikey or yf_ticker):
            return pd.DataFrame()
        cache_key = f"daily_{ikey or yf_ticker}_{lookback_days}"

        def _fetch() -> pd.DataFrame:
            # We deliberately do NOT blacklist on empty responses any more.
            # Upstox sometimes returns HTTP 200 with `{"data":{"candles":[]}}`
            # when rate-limited or under brief maintenance — blacklisting on
            # that would slowly empty the universe over a few runs.
            # The transient errors path also just returns empty, no blacklist.
            if not (self.upstox and ikey):
                return pd.DataFrame()
            try:
                df = self.upstox.candles(
                    ikey, "day",
                    from_date=date.today() - timedelta(days=lookback_days),
                )
                if df.empty:
                    log.debug(f"⚠ upstox empty {ikey} (possibly rate-limit or delisted)")
                    return pd.DataFrame()
                log.debug(f"📈 upstox ✓ {yf_ticker} ({ikey}) — {len(df)} bars")
                return df
            except Exception as e:
                log.debug(f"⚠ upstox candles {ikey} fail: {type(e).__name__}: {e}")
                return pd.DataFrame()

        return get_or_set("daily", cache_key, ttl_seconds=60 * 60 * 6, fn=_fetch)

    # ---------- intraday OHLCV ----------
    def intraday(self, yf_ticker: str, instrument_key: Optional[str],
                 interval: str = "30minute") -> pd.DataFrame:
        ikey = self._resolve_key(instrument_key, yf_ticker)
        if is_blacklisted(ikey or yf_ticker):
            return pd.DataFrame()
        if not (self.upstox and ikey):
            return pd.DataFrame()
        try:
            up_iv = {"1m": "1minute", "5m": "30minute",
                     "30m": "30minute"}.get(interval, interval)
            df = self.upstox.intraday_candles(ikey, up_iv)
            return df
        except Exception as e:
            log.debug(f"⚠ upstox intraday {ikey} failed: {e}")
            return pd.DataFrame()

    # ---------- LTP / quote ----------
    def ltp(self, yf_ticker: str, instrument_key: Optional[str] = None) -> Optional[float]:
        ikey = self._resolve_key(instrument_key, yf_ticker)
        if not (self.upstox and ikey):
            return None
        try:
            resp = self.upstox.ltp([ikey])
            for v in (resp or {}).values():
                if "last_price" in v:
                    return float(v["last_price"])
        except Exception as e:
            log.debug(f"upstox LTP failed {ikey}: {e}")
        return None

    def quote(self, yf_ticker: str, instrument_key: Optional[str] = None) -> dict:
        """Full snapshot from Upstox: OHLC + day range + last price + volume + OI."""
        ikey = self._resolve_key(instrument_key, yf_ticker)
        if not (self.upstox and ikey):
            return {}
        try:
            resp = self.upstox.quote([ikey])
            for v in (resp or {}).values():
                return v
        except Exception as e:
            log.debug(f"upstox quote failed {ikey}: {e}")
        return {}

    # ---------- "fundamentals" — Upstox doesn't expose FA data ----------
    # We return whatever the Upstox quote endpoint provides (OHLC, day/52w
    # range, volume). The agent prompt has been updated to make trade decisions
    # on technicals + news + this snapshot, NOT P/E or ROE which we no longer
    # have access to.
    def fundamentals(self, yf_ticker: str, instrument_key: Optional[str] = None) -> dict:
        q = self.quote(yf_ticker, instrument_key)
        if not q:
            return {}
        # Flatten the most useful fields. Upstox response shape varies; be liberal.
        ohlc = q.get("ohlc", {}) or {}
        return {
            "source": "upstox_quote",
            "last_price": q.get("last_price"),
            "volume": q.get("volume"),
            "open": ohlc.get("open"),
            "high": ohlc.get("high"),
            "low": ohlc.get("low"),
            "close": ohlc.get("close"),
            "day_change_pct": q.get("net_change"),
            "year_high": q.get("year_high") or q.get("52_week_high"),
            "year_low": q.get("year_low") or q.get("52_week_low"),
            "avg_volume": q.get("average_volume"),
            "oi": q.get("oi"),
            "note": "Upstox API does not expose P/E, ROE, or growth metrics. "
                    "Judge based on price action, OI shifts, and news.",
        }

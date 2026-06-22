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


class _DataBreaker:
    """Process-wide circuit breaker for broker market-data calls.

    After `threshold` consecutive broker failures (e.g. Groww live-data/historical
    403), the breaker OPENS and every subsequent price/candle request skips the
    broker and goes straight to the Yahoo fallback — so a long run (DR-Quant,
    Universe Map) doesn't keep hammering a broker that clearly can't serve data.
    Any broker success closes it again.
    """
    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self.fails = 0

    @property
    def open(self) -> bool:
        return self.fails >= self.threshold

    def fail(self):
        self.fails += 1
        if self.fails == self.threshold:
            log.warning(f"[breaker] broker market-data failed {self.fails}× — "
                        f"switching to Yahoo fallback for the rest of this run")

    def ok(self):
        if self.fails:
            self.fails = 0


# Module-level so it persists across MarketData instances within a process/run.
_DATA_BREAKER = _DataBreaker(threshold=3)


def reset_data_breaker():
    _DATA_BREAKER.fails = 0


class MarketData:
    def __init__(self, upstox: Optional[UpstoxClient] = None):
        if upstox is not None:
            self.upstox = upstox
        else:
            try:
                self.upstox = get_broker()
            except Exception as e:
                log.warning(f"broker client unavailable: {e}")
                self.upstox = None
        # Groww uses bare trading symbols, not Upstox 'NSE_EQ|INE…' keys.
        self._is_groww = type(self.upstox).__name__ == "GrowwClient"

    # ---------- helpers ----------
    def _resolve_key(self, instrument_key: Optional[str],
                     yf_ticker: Optional[str]) -> Optional[str]:
        # Groww path: identify by the bare trading symbol (RELIANCE), derived
        # from yf_ticker ('RELIANCE.NS'). An Upstox 'NSE_EQ|INE…' key is useless
        # to Groww, so we never feed it one.
        if self._is_groww:
            base = (yf_ticker or instrument_key or "")
            base = base.replace(".NS", "").replace(".BO", "").strip().upper()
            if not base or "|" in base:
                return None
            return base
        # Upstox path (unchanged)
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
            # Skip the broker entirely once the breaker has tripped this run.
            if self.upstox and ikey and not _DATA_BREAKER.open:
                try:
                    df = self.upstox.candles(
                        ikey, "day",
                        from_date=date.today() - timedelta(days=lookback_days),
                    )
                    if not df.empty:
                        _DATA_BREAKER.ok()
                        log.debug(f"📈 broker ✓ {yf_ticker} ({ikey}) — {len(df)} bars")
                        return df
                    _DATA_BREAKER.fail()
                    log.debug(f"⚠ broker empty {ikey} — trying Yahoo fallback")
                except Exception as e:
                    _DATA_BREAKER.fail()
                    log.debug(f"⚠ broker candles {ikey} fail ({type(e).__name__}: {e}) — Yahoo fallback")
            # Failproof: broker couldn't serve candles (Groww historical 403 / not
            # entitled, no Upstox auth, breaker open, etc.) → free public source.
            return self._yahoo_daily(yf_ticker, lookback_days)

        return get_or_set("daily", cache_key, ttl_seconds=60 * 60 * 6, fn=_fetch)

    @staticmethod
    def _yahoo_daily(yf_ticker: Optional[str], lookback_days: int = 365) -> pd.DataFrame:
        if not yf_ticker:
            return pd.DataFrame()
        try:
            from src.data import yahoo
            df = yahoo.daily(yf_ticker, lookback_days)
            if not df.empty:
                log.debug(f"📈 yahoo ✓ {yf_ticker} — {len(df)} bars")
            return df
        except Exception as e:
            log.debug(f"yahoo daily {yf_ticker} failed: {e}")
            return pd.DataFrame()

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
        if self.upstox and ikey and not _DATA_BREAKER.open:
            try:
                resp = self.upstox.ltp([ikey])
                for v in (resp or {}).values():
                    if "last_price" in v:
                        _DATA_BREAKER.ok()
                        return float(v["last_price"])
                _DATA_BREAKER.fail()
            except Exception as e:
                _DATA_BREAKER.fail()
                log.debug(f"broker LTP failed {ikey}: {e} — Yahoo fallback")
        # Failproof fallback
        try:
            from src.data import yahoo
            return yahoo.ltp(yf_ticker)
        except Exception:
            return None

    def quote(self, yf_ticker: str, instrument_key: Optional[str] = None) -> dict:
        """Full snapshot: OHLC + last price + volume. Broker first, Yahoo fallback."""
        ikey = self._resolve_key(instrument_key, yf_ticker)
        if self.upstox and ikey and not _DATA_BREAKER.open:
            try:
                resp = self.upstox.quote([ikey])
                for v in (resp or {}).values():
                    if v:
                        _DATA_BREAKER.ok()
                        return v
                _DATA_BREAKER.fail()
            except Exception as e:
                _DATA_BREAKER.fail()
                log.debug(f"broker quote failed {ikey}: {e} — Yahoo fallback")
        try:
            from src.data import yahoo
            return yahoo.quote(yf_ticker)
        except Exception:
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

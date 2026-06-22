"""Free public market-data fallback (Yahoo Finance v8 chart).

Used whenever the active broker can't serve prices/candles — e.g. Groww's
live-data/historical endpoints return 403 (the data feed isn't entitled), or
Upstox is unauthenticated. Plain `requests` only (no yfinance dependency), so
it works on-device (Chaquopy) too. No API key or cookie required.

Endpoint:
  GET https://query1.finance.yahoo.com/v8/finance/chart/{TICKER}
      ?period1=<epoch>&period2=<epoch>&interval=1d
  → result[0].timestamp[], result[0].indicators.quote[0].{open,high,low,close,volume},
    result[0].meta.{regularMarketPrice, chartPreviousClose}
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
import requests

from src.utils.logger import get_logger

log = get_logger("data.yahoo")

_HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PortfolioQuant/1.0)"}


def _candidates(ticker: str) -> list[str]:
    """NSE first, then BSE, accepting bare symbols or already-suffixed ones."""
    t = (ticker or "").strip().upper()
    if not t:
        return []
    if t.endswith(".NS") or t.endswith(".BO"):
        return [t]
    base = t.replace(".NS", "").replace(".BO", "")
    return [f"{base}.NS", f"{base}.BO"]


def _chart(ticker: str, params: dict) -> Optional[dict]:
    for sym in _candidates(ticker):
        for host in _HOSTS:
            try:
                r = requests.get(f"{host}/v8/finance/chart/{sym}",
                                 params=params, headers=_HEADERS, timeout=12)
                if r.status_code != 200:
                    continue
                res = (r.json().get("chart") or {}).get("result") or []
                if res:
                    return res[0]
            except Exception as e:
                log.debug(f"yahoo {sym} @ {host} failed: {e}")
                continue
    return None


def daily(ticker: str, lookback_days: int = 365) -> pd.DataFrame:
    """Daily OHLCV DataFrame indexed by timestamp (columns: open/high/low/close/volume/oi)."""
    end = int(time.time())
    start = int((datetime.now() - timedelta(days=lookback_days + 5)).timestamp())
    res = _chart(ticker, {"period1": start, "period2": end, "interval": "1d"})
    if not res:
        return pd.DataFrame()
    ts = res.get("timestamp") or []
    q = (((res.get("indicators") or {}).get("quote") or [{}])[0]) or {}
    if not ts or not q.get("close"):
        return pd.DataFrame()
    df = pd.DataFrame({
        "ts": pd.to_datetime(ts, unit="s"),
        "open": q.get("open"), "high": q.get("high"), "low": q.get("low"),
        "close": q.get("close"), "volume": q.get("volume"),
    }).dropna(subset=["close"])
    if df.empty:
        return df
    df["oi"] = 0
    return df.sort_values("ts").set_index("ts")


def ltp(ticker: str) -> Optional[float]:
    res = _chart(ticker, {"interval": "1d", "range": "1d"})
    if not res:
        return None
    px = (res.get("meta") or {}).get("regularMarketPrice")
    return float(px) if px is not None else None


def quote(ticker: str) -> dict:
    """Snapshot mirroring the broker quote shape (last_price + ohlc + prev close)."""
    res = _chart(ticker, {"interval": "1d", "range": "5d"})
    if not res:
        return {}
    meta = res.get("meta") or {}
    q = (((res.get("indicators") or {}).get("quote") or [{}])[0]) or {}
    def _last(arr):
        vals = [x for x in (arr or []) if x is not None]
        return vals[-1] if vals else None
    return {
        "last_price": meta.get("regularMarketPrice"),
        "ohlc": {
            "open": _last(q.get("open")), "high": _last(q.get("high")),
            "low": _last(q.get("low")), "close": meta.get("chartPreviousClose"),
        },
        "previous_close": meta.get("chartPreviousClose"),
        "volume": _last(q.get("volume")),
        "source": "yahoo",
    }

"""Stage-1 technical bulk filter.

If TA-Lib is installed we use it; otherwise we fall back to the pure-pandas
indicators in `src/utils/indicators.py`. The output is a ranked list of
"high-probability" candidates by setup type.

Setups detected:
- RSI bullish divergence (price LL, RSI HL)
- Volume breakout (close > 20D high, volume > 1.5× 20D avg)
- Wyckoff Phase-C reaccumulation (price wicks below 30D low then recovers)
- Trend-momentum (EMA20 > EMA50 > EMA200 and RSI 50-70)
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import numpy as np
import pandas as pd
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

from src.data import MarketData
from src.data.instruments import resolve_universe
from src.upstox.client import UpstoxClient
from src.brokers import get_broker
from src.utils.indicators import ema, rsi
from src.utils.logger import get_logger

log = get_logger("screener.talib")

try:
    import talib  # type: ignore
    HAS_TALIB = True
except Exception:
    HAS_TALIB = False


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    if HAS_TALIB:
        return pd.Series(talib.RSI(close.values, n), index=close.index)
    return rsi(close, n)


def _setups(df: pd.DataFrame) -> dict:
    if df is None or df.empty or len(df) < 60:
        return {}
    c, v = df["close"], df["volume"]
    setups = {}

    # 1. Volume breakout
    h20 = c.rolling(20).max().shift(1)
    v20 = v.rolling(20).mean().shift(1)
    if c.iloc[-1] > (h20.iloc[-1] or 0) and v.iloc[-1] > 1.5 * (v20.iloc[-1] or 1):
        setups["volume_breakout"] = True

    # 2. Trend-momentum
    e20, e50, e200 = ema(c, 20).iloc[-1], ema(c, 50).iloc[-1], ema(c, 200).iloc[-1]
    r = _rsi(c).iloc[-1]
    if e20 > e50 > e200 and 50 <= (r or 0) <= 70:
        setups["trend_momentum"] = True

    # 3. RSI bullish divergence (last 20 bars)
    tail = c.tail(20)
    rtail = _rsi(c).tail(20)
    if not tail.empty and not rtail.empty:
        if tail.iloc[-1] < tail.min() * 1.01 and rtail.iloc[-1] > rtail.min() * 1.05:
            setups["rsi_bull_div"] = True

    # 4. Wyckoff Phase-C reaccumulation (spring + recovery)
    l30 = c.rolling(30).min()
    if df["low"].iloc[-2] < l30.iloc[-2] and c.iloc[-1] > c.iloc[-2] * 1.01:
        setups["wyckoff_spring"] = True

    return setups


class TALibScreener:
    def __init__(self, upstox: Optional[UpstoxClient] = None, workers: int = 8):
        try:
            self.up = upstox or get_broker()
        except Exception:
            self.up = None
        self.md = MarketData(self.up)
        self.workers = workers

    def _row(self, name, yf_t, nse_t, ikey) -> Optional[dict]:
        try:
            df = self.md.daily(yf_t, ikey, lookback_days=400)
            setups = _setups(df)
            if not setups:
                return None
            row = {
                "name": str(name), "symbol": nse_t,
                "instrument_key": ikey, "yf_ticker": yf_t,
                "ltp": float(df["close"].iloc[-1]),
                "setups": list(setups.keys()),
                "score": len(setups) * 25 + int(_rsi(df["close"]).iloc[-1] or 0) // 2,
            }
            log.info(f"  ✓ {nse_t}: setups={list(setups.keys())} score={row['score']}")
            return row
        except Exception as e:
            log.debug(f"  ✗ {nse_t}: {e}")
            return None

    def scan(self, universe: str = "nifty50", top_n: int = 20) -> pd.DataFrame:
        items = resolve_universe(universe)

        # Stability fix for Android: avoid heavy threading
        import os
        is_android = os.getenv("APP_FILES_DIR") is not None
        actual_workers = 1 if is_android else self.workers

        log.info(f"TA-Lib scan: {len(items)} instruments (talib={HAS_TALIB}, workers={actual_workers})")
        rows = []
        with Progress(TextColumn("[cyan]talib"), BarColumn(),
                      TextColumn("{task.completed}/{task.total}"),
                      TimeRemainingColumn()) as prog:
            t = prog.add_task("scan", total=len(items))
            with ThreadPoolExecutor(max_workers=actual_workers) as ex:
                for fut in as_completed(ex.submit(self._row, *it) for it in items):
                    r = fut.result()
                    if r:
                        rows.append(r)
                    prog.advance(t)
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).sort_values("score", ascending=False).head(top_n)

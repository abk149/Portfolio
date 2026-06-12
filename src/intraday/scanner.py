"""Live intraday opportunity scanner.

Strategies implemented:
- Opening-Range Breakout (ORB) on 15m bar
- Momentum + volume surge vs 20-day average
- VWAP reclaim
- Gap-up with follow-through

Each candidate gets a score, suggested entry, stop, and 1R/2R targets.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from src.data import MarketData
from src.data.instruments import resolve_universe
from src.upstox.client import UpstoxClient
from src.utils.indicators import atr, vwap, rsi
from src.utils.logger import get_logger

log = get_logger("intraday.scanner")


class IntradayScanner:
    def __init__(self, upstox: Optional[UpstoxClient] = None):
        try:
            self.upstox = upstox or UpstoxClient()
        except Exception:
            self.upstox = None
        self.md = MarketData(self.upstox)

    def _evaluate(self, name, yf_t, nse_t, ikey) -> Optional[dict]:
        daily = self.md.daily(yf_t, ikey, lookback_days=60)
        intra = self.md.intraday(yf_t, ikey, interval="5m")
        if daily.empty or intra.empty or len(intra) < 6:
            return None

        prev_close = daily["close"].iloc[-2] if len(daily) >= 2 else daily["close"].iloc[-1]
        today = intra[intra.index.date == intra.index.date.max()]
        if today.empty:
            today = intra.tail(78)  # roughly 1 day of 5m bars

        open_price = today["open"].iloc[0]
        last = today["close"].iloc[-1]
        gap_pct = (open_price - prev_close) / prev_close * 100

        orb = today.iloc[:3]  # first 15 minutes
        orb_high, orb_low = orb["high"].max(), orb["low"].min()

        avg_vol_20 = daily["volume"].tail(20).mean()
        today_vol = today["volume"].sum()
        vol_ratio = today_vol / max(avg_vol_20, 1)

        vw = vwap(today).iloc[-1]
        r = rsi(today["close"], 14).iloc[-1] if len(today) > 14 else np.nan
        a = atr(daily, 14).iloc[-1]

        signals = []
        score = 0
        direction = None

        if last > orb_high and gap_pct > -0.5:
            signals.append("ORB-breakout-long")
            direction = "LONG"
            score += 25
        elif last < orb_low and gap_pct < 0.5:
            signals.append("ORB-breakdown-short")
            direction = "SHORT"
            score += 25

        if vol_ratio > 1.5:
            signals.append(f"volume-surge x{vol_ratio:.1f}")
            score += 15
        if gap_pct > 1 and last > open_price:
            signals.append(f"gap-up {gap_pct:.1f}% with follow-through")
            direction = direction or "LONG"
            score += 15
        if gap_pct < -1 and last < open_price:
            signals.append(f"gap-down {gap_pct:.1f}% with follow-through")
            direction = direction or "SHORT"
            score += 15
        if last > vw and direction != "SHORT":
            signals.append("above-VWAP")
            score += 10
        elif last < vw and direction != "LONG":
            signals.append("below-VWAP")
            score += 10
        if not np.isnan(r):
            if 55 <= r <= 75 and direction == "LONG":
                signals.append(f"RSI {r:.0f} momentum")
                score += 10
            elif 25 <= r <= 45 and direction == "SHORT":
                signals.append(f"RSI {r:.0f} weakness")
                score += 10

        if not signals or direction is None:
            return None

        stop = orb_low if direction == "LONG" else orb_high
        risk = abs(last - stop)
        if risk < 1e-6:
            return None
        t1 = last + (1 if direction == "LONG" else -1) * risk
        t2 = last + (1 if direction == "LONG" else -1) * 2 * risk

        return {
            "name": name, "symbol": nse_t,
            "direction": direction, "score": score,
            "ltp": round(float(last), 2), "entry": round(float(last), 2),
            "stop": round(float(stop), 2),
            "target_1R": round(float(t1), 2),
            "target_2R": round(float(t2), 2),
            "risk_per_share": round(float(risk), 2),
            "gap_pct": round(float(gap_pct), 2),
            "vol_ratio": round(float(vol_ratio), 2),
            "atr": round(float(a), 2) if not np.isnan(a) else None,
            "signals": signals,
        }

    def scan(self, universe: str = "nifty50", min_score: int = 40) -> pd.DataFrame:
        rows = []
        for u in resolve_universe(universe):
            try:
                r = self._evaluate(*u)
                if r and r["score"] >= min_score:
                    rows.append(r)
            except Exception as e:
                log.debug(f"scan err {u[0]}: {e}")
        return pd.DataFrame(rows).sort_values("score", ascending=False) if rows else pd.DataFrame()

    def scan_tickers(self, yf_tickers: list[str], min_score: int = 40) -> pd.DataFrame:
        """Evaluate a specific list of yf tickers (no universe download).
        Used by Stage 4 — far faster than scanning all 3000+ NSE names."""
        from src.data.instruments import resolve_instrument_key
        rows = []
        for yf_t in yf_tickers:
            nse = yf_t.replace(".NS", "").replace(".BO", "")
            ikey = resolve_instrument_key(yf_t) or ""
            try:
                r = self._evaluate(nse, yf_t, nse, ikey)
                if r and r["score"] >= min_score:
                    rows.append(r)
            except Exception as e:
                log.debug(f"intraday eval {yf_t}: {e}")
        return pd.DataFrame(rows).sort_values("score", ascending=False) if rows else pd.DataFrame()

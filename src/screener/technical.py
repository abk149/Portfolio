"""Technical scoring: returns a dict of signals + a 0-100 score for one symbol."""
from __future__ import annotations

import pandas as pd

from src.utils.indicators import ema, rsi, macd, atr


def technical_score(df: pd.DataFrame) -> dict:
    if df is None or df.empty or len(df) < 60:
        return {"score": None, "reason": "insufficient data"}

    close = df["close"]
    e20, e50, e200 = ema(close, 20), ema(close, 50), ema(close, 200)
    r = rsi(close, 14)
    m = macd(close)
    a = atr(df, 14)

    last = close.iloc[-1]
    signals = {
        "price_above_ema50": bool(last > e50.iloc[-1]),
        "price_above_ema200": bool(last > e200.iloc[-1]),
        "ema20_above_ema50": bool(e20.iloc[-1] > e50.iloc[-1]),
        "golden_cross_recent": bool((e50.iloc[-20:] > e200.iloc[-20:]).sum() > 0 and (e50.iloc[-21] <= e200.iloc[-21])),
        "rsi": float(r.iloc[-1]) if not pd.isna(r.iloc[-1]) else None,
        "rsi_healthy": bool(40 < (r.iloc[-1] or 0) < 70),
        "macd_bullish": bool(m["hist"].iloc[-1] > 0 and m["hist"].iloc[-1] > m["hist"].iloc[-2]),
        "ret_1m_pct": float(close.pct_change(21).iloc[-1] * 100),
        "ret_3m_pct": float(close.pct_change(63).iloc[-1] * 100),
        "ret_6m_pct": float(close.pct_change(126).iloc[-1] * 100) if len(close) > 126 else None,
        "atr_pct": float((a.iloc[-1] / last) * 100) if not pd.isna(a.iloc[-1]) else None,
        "near_52w_high_pct": float((last / close.tail(252).max()) * 100) if len(close) >= 60 else None,
    }

    score = 0
    score += 15 if signals["price_above_ema200"] else 0
    score += 10 if signals["price_above_ema50"] else 0
    score += 10 if signals["ema20_above_ema50"] else 0
    score += 10 if signals["golden_cross_recent"] else 0
    score += 10 if signals["rsi_healthy"] else 0
    score += 10 if signals["macd_bullish"] else 0
    score += min(15, max(0, (signals["ret_3m_pct"] or 0) / 2))
    score += min(10, max(0, (signals["ret_1m_pct"] or 0)))
    if signals["near_52w_high_pct"] and signals["near_52w_high_pct"] > 90:
        score += 10
    signals["score"] = round(min(100, max(0, score)), 1)
    return signals

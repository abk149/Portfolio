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

    # ---- trend quality: is the uptrend intact? ----
    score = 0
    score += 15 if signals["price_above_ema200"] else 0
    score += 10 if signals["price_above_ema50"] else 0
    score += 10 if signals["ema20_above_ema50"] else 0
    score += 10 if signals["golden_cross_recent"] else 0
    score += 10 if signals["macd_bullish"] else 0

    # ---- how much of the move is LEFT ----
    #
    # This used to add up to 25 points for recent returns and another 10 for
    # sitting within 10% of the 52-week high — so the scanner systematically
    # ranked stocks HIGHER the more they had already run, and surfaced them
    # after the move rather than before it. By the time a reason is visible in
    # the price, the trade has largely been taken.
    #
    # Momentum still matters (a falling knife is not a bargain), so the trend
    # block above is unchanged. What changed is that momentum is now rewarded
    # in a BAND and punished at the extremes: enough to confirm the trend, not
    # so much that the move is over.
    r3 = signals["ret_3m_pct"] or 0.0
    r1 = signals["ret_1m_pct"] or 0.0
    near_high = signals["near_52w_high_pct"]
    rsi_now = signals["rsi"] or 50.0

    # 3-month: best around +5% to +20%. Flat/negative earns little, parabolic
    # is penalised.
    if r3 < -20:
        score += 0                       # broken, not a pullback
    elif r3 < 0:
        score += 6
    elif r3 <= 20:
        score += 15
    elif r3 <= 35:
        score += 8
    else:
        score -= 6                       # already ran

    # 1-month: a vertical month is a reason for caution, not a reward.
    if r1 > 20:
        score -= 8
    elif r1 > 12:
        score -= 3
    elif r1 >= -5:
        score += 6

    # Position in the 52-week range: an uptrend that has paused beats one
    # printing new highs every session.
    if near_high is not None:
        if near_high >= 98:
            score -= 5                   # at the high — late
        elif near_high >= 85:
            score += 12                  # strong, with a little room
        elif near_high >= 70:
            score += 8                   # meaningful pullback in an uptrend
        elif near_high >= 55:
            score += 3
        else:
            score += 0                   # far below — trend likely broken

    # RSI: healthy beats hot.
    if 45 <= rsi_now <= 65:
        score += 12
    elif 40 <= rsi_now < 45 or 65 < rsi_now <= 70:
        score += 6
    elif rsi_now > 75:
        score -= 8

    # Surfaced so downstream stages can see WHY, and filter on it.
    stretched = []
    if r3 > 35:
        stretched.append(f"up {r3:.0f}% in 3 months")
    if r1 > 20:
        stretched.append(f"up {r1:.0f}% in a month")
    if near_high is not None and near_high >= 98:
        stretched.append("at its 52-week high")
    if rsi_now > 75:
        stretched.append(f"overbought (RSI {rsi_now:.0f})")
    signals["already_moved"] = stretched
    signals["move_left"] = not stretched

    signals["score"] = round(min(100, max(0, score)), 1)
    return signals

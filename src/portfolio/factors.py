"""What actually drives a stock, and how much of the move is already gone.

Two questions the rest of the system kept answering implicitly:

  **What is this exposed to?** A regression of the stock's weekly returns on the
  market, the rupee, crude and US yields. The output is readable as "a 1% move
  in USD/INR has historically moved this stock X%", which is the form the
  question gets asked in.

  **Is there any move left?** Suggesting a name that has already run is the
  easiest way to look clever and lose money — by the time the reason is
  visible in the price, the trade is gone. `runway()` measures how much of the
  move has happened: recent returns, distance from the 52-week high, stretch
  above the moving averages, RSI.

Both are pure price arithmetic — no model, no news — so they are reproducible
and cheap, and they say the same thing every time for the same inputs.

One implementation trap worth naming: Yahoo stamps each bar at the market OPEN
in that market's timezone, so Indian equities, the rupee, crude and US yields
all arrive on different timestamps. Aligning them without normalising to plain
dates silently yields an empty intersection and a NaN correlation, which looks
like "no relationship" rather than "no overlap".
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

from src.utils.logger import get_logger

log = get_logger("portfolio.factors")

# The forces an Indian equity is usually exposed to, in the form Yahoo serves.
FACTORS: dict[str, dict] = {
    "nifty": {"ticker": "^NSEI", "label": "Nifty 50",
              "reads": "the market as a whole"},
    "usdinr": {"ticker": "USDINR=X", "label": "USD/INR",
               "reads": "a weaker rupee (importers hurt, exporters gain)"},
    "crude": {"ticker": "CL=F", "label": "Crude oil",
              "reads": "higher oil (input costs, the import bill)"},
    "us_yield": {"ticker": "^TNX", "label": "US 10-year yield",
                 "reads": "higher global rates (FII flows, discount rates)"},
}


def _closes(ticker: str, lookback_days: int) -> pd.Series:
    """Daily closes on a tz-naive, midnight-normalised index."""
    try:
        from src.data import yahoo
        df = yahoo.daily(ticker, lookback_days)
    except Exception as e:
        log.debug(f"factor fetch failed for {ticker}: {e}")
        return pd.Series(dtype=float)
    if df is None or df.empty or "close" not in df:
        return pd.Series(dtype=float)
    s = pd.to_numeric(df["close"], errors="coerce").dropna()
    idx = pd.to_datetime(s.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("Asia/Kolkata").tz_localize(None)
    # Normalising to the calendar date is what makes markets in different
    # timezones line up at all.
    s.index = idx.normalize()
    return s[~s.index.duplicated(keep="last")].sort_index()


def _weekly_returns(s: pd.Series) -> pd.Series:
    """Weekly is the right frequency here: daily is mostly noise for a
    cross-market regression, monthly leaves too few points in a few years."""
    if s.empty:
        return s
    g = s.groupby([s.index.isocalendar().year, s.index.isocalendar().week]).last()
    return g.pct_change(fill_method=None).dropna()


def sensitivities(symbol: str, years: int = 3) -> dict:
    """How this stock has moved with the market, the rupee, crude and US rates.

    Univariate betas are reported alongside the joint regression: the factors
    are correlated with each other, so the joint coefficient answers "holding
    the rest still" while the simple one answers "when this moves, what
    happens" — and they can disagree in informative ways.
    """
    sym = (symbol or "").upper().replace(".NS", "").replace(".BO", "")
    lookback = int(years * 365.25) + 30

    stock = _weekly_returns(_closes(f"{sym}.NS", lookback))
    if stock.empty or len(stock) < 30:
        return {"symbol": sym,
                "error": "Not enough price history to measure factor exposure."}

    series = {}
    for key, meta in FACTORS.items():
        r = _weekly_returns(_closes(meta["ticker"], lookback))
        if not r.empty:
            series[key] = r

    if not series:
        return {"symbol": sym, "error": "Couldn't load any macro factor data."}

    frame = pd.DataFrame({"stock": stock, **series}).dropna()
    if len(frame) < 30:
        return {"symbol": sym,
                "error": f"Only {len(frame)} overlapping weeks — too few to "
                         f"measure factor exposure."}

    y = frame["stock"].to_numpy()
    rows = []
    for key in series:
        x = frame[key].to_numpy()
        if np.std(x) < 1e-12:
            continue
        beta = float(np.cov(y, x, ddof=1)[0][1] / np.var(x, ddof=1))
        corr = float(np.corrcoef(y, x)[0][1])
        # t of the correlation: n-2 degrees of freedom.
        n = len(y)
        t = (corr * math.sqrt(n - 2) / math.sqrt(1 - corr ** 2)
             if abs(corr) < 0.999 else None)
        rows.append({
            "factor": key, "label": FACTORS[key]["label"],
            "reads": FACTORS[key]["reads"],
            "beta": round(beta, 2), "correlation": round(corr, 2),
            "t_stat": round(t, 2) if t is not None else None,
            "material": bool(t is not None and abs(t) >= 2.0 and abs(beta) >= 0.15),
        })

    # Joint fit, so overlapping factors don't each take credit for the same move.
    cols = [r["factor"] for r in rows]
    joint = {}
    r2 = None
    if cols:
        X = np.column_stack([frame[c].to_numpy() for c in cols] + [np.ones(len(frame))])
        try:
            coef, *_ = np.linalg.lstsq(X, y, rcond=None)
            fitted = X @ coef
            ss_res = float(np.sum((y - fitted) ** 2))
            ss_tot = float(np.sum((y - np.mean(y)) ** 2))
            r2 = round(1 - ss_res / ss_tot, 3) if ss_tot > 0 else None
            joint = {c: round(float(b), 2) for c, b in zip(cols, coef[:-1])}
        except Exception as e:
            log.debug(f"joint regression failed for {sym}: {e}")

    for r in rows:
        r["beta_joint"] = joint.get(r["factor"])

    material = [r for r in rows if r["material"]]
    rows.sort(key=lambda r: -abs(r["correlation"] or 0))

    return {
        "symbol": sym, "weeks": int(len(frame)), "years": years,
        "factors": rows,
        "r_squared": r2,
        "driven_by": [r["label"] for r in material] or [],
        "reading": _sensitivity_reading(material, r2),
        "caveat": ("Measured on weekly moves over the sample shown. A beta is "
                   "what HAS happened, not a promise — exposures shift when a "
                   "company's mix or debt changes."),
    }


def _sensitivity_reading(material: list[dict], r2: Optional[float]) -> str:
    if not material:
        return ("No macro factor explains this stock's moves to a meaningful "
                "degree — it trades on its own news more than on the market, "
                "the rupee, crude or global rates.")
    bits = []
    for r in material:
        direction = "rises" if r["beta"] > 0 else "falls"
        bits.append(f"{direction} about {abs(r['beta']):.2f}% for each 1% move "
                    f"in {r['label']}")
    tail = (f" Together the factors explain about {r2 * 100:.0f}% of its weekly "
            f"moves." if r2 is not None else "")
    return "Historically " + "; ".join(bits) + "." + tail


def runway(symbol: str) -> dict:
    """How much of the move has already happened.

    The policy this serves: prefer names with room left. Once the reason is
    visible in the price, the trade has mostly been taken — so a stock that has
    already run hard, sits on its 52-week high and is overbought is penalised
    even when the story is good.
    """
    sym = (symbol or "").upper().replace(".NS", "").replace(".BO", "")
    s = _closes(f"{sym}.NS", 500)
    if s.empty or len(s) < 60:
        return {"symbol": sym, "error": "Not enough price history."}

    from src.utils.indicators import rsi, sma

    cur = float(s.iloc[-1])

    def _ret(days: int) -> Optional[float]:
        past = s[s.index <= s.index[-1] - pd.Timedelta(days=days)]
        if past.empty:
            return None
        return round((cur / float(past.iloc[-1]) - 1) * 100, 2)

    window52 = s.tail(252)
    hi52, lo52 = float(window52.max()), float(window52.min())
    from_high = round((cur / hi52 - 1) * 100, 2) if hi52 else None
    off_low = round((cur / lo52 - 1) * 100, 2) if lo52 else None

    r = float(rsi(s, 14).iloc[-1]) if len(s) >= 15 else None
    d50 = float(sma(s, 50).iloc[-1]) if len(s) >= 50 else None
    d200 = float(sma(s, 200).iloc[-1]) if len(s) >= 200 else None
    above50 = round((cur / d50 - 1) * 100, 2) if d50 else None
    above200 = round((cur / d200 - 1) * 100, 2) if d200 else None

    r3m, r6m, r12m = _ret(90), _ret(180), _ret(365)

    # Each condition is a separate way of saying "the move already happened".
    flags = []
    if (r3m or 0) >= 25:
        flags.append(f"up {r3m:.0f}% in three months")
    if (r6m or 0) >= 40:
        flags.append(f"up {r6m:.0f}% in six months")
    if from_high is not None and from_high >= -3:
        flags.append("sitting on its 52-week high")
    if r is not None and r >= 70:
        flags.append(f"overbought (RSI {r:.0f})")
    if above200 is not None and above200 >= 35:
        flags.append(f"{above200:.0f}% above its 200-day average")

    room = []
    if from_high is not None and from_high <= -20:
        room.append(f"{abs(from_high):.0f}% below its 52-week high")
    if r is not None and r <= 45:
        room.append(f"not overbought (RSI {r:.0f})")
    if above200 is not None and -10 <= above200 <= 12:
        room.append("close to its long-term average")
    if (r3m or 0) <= 5:
        room.append("hasn't run in the last quarter")

    # Score in [0, 100]: higher means more of the move is still ahead.
    score = 60.0
    score -= 8.0 * len(flags)
    score += 6.0 * len(room)
    if from_high is not None:
        score += max(min(-from_high, 40), 0) * 0.5      # further below the high
    if r is not None:
        score -= max(r - 55, 0) * 0.8                   # penalise stretch
    score = float(max(0.0, min(100.0, score)))

    if flags and score < 40:
        stance = "move largely made"
    elif score >= 65:
        stance = "room left"
    else:
        stance = "mixed"

    return {
        "symbol": sym, "price": round(cur, 2),
        "ret_1m": _ret(30), "ret_3m": r3m, "ret_6m": r6m, "ret_12m": r12m,
        "from_52w_high_pct": from_high, "off_52w_low_pct": off_low,
        "rsi": round(r, 1) if r is not None else None,
        "above_50dma_pct": above50, "above_200dma_pct": above200,
        "already_moved": flags, "room_left": room,
        "runway_score": round(score, 1), "stance": stance,
        "reading": _runway_reading(stance, flags, room),
    }


def _runway_reading(stance: str, flags: list[str], room: list[str]) -> str:
    if stance == "move largely made":
        return ("Most of this move looks done — " + ", ".join(flags) + ". "
                "Buying here is paying for news the market already has.")
    if stance == "room left":
        return ("There looks to be room: " + (", ".join(room) or "nothing stretched") +
                ". The thesis hasn't been priced in yet.")
    return ("Partly priced in" + (" — " + ", ".join(flags) if flags else "") +
            ". Neither obviously early nor obviously late.")

"""Month-by-month seasonality: is this stock actually cyclical?

Splits several years of history into calendar months and asks whether any month
behaves differently from the rest — the question behind "can I time this one".

**The honesty problem this has to solve.** Five years of history gives FIVE
observations per calendar month. A month averaging +4% across five years is
entirely consistent with noise, and a chart of twelve monthly averages will
always show some months looking good, because that is what randomness looks
like. Presenting that as a tradeable pattern would be the most misleading thing
in the app.

So every month carries its sample size and a t-statistic, the verdict requires
BOTH a meaningful average AND consistency across years, and when the evidence
is thin the module says so rather than letting a suggestive-looking bar chart
speak for itself.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from src.utils.logger import get_logger

log = get_logger("portfolio.seasonality")

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

MIN_YEARS = 3          # below this, don't offer a verdict at all

# Calibrated, not guessed. Twelve months are examined at once, so the chance
# that SOMETHING looks significant by luck is high — the classic
# multiple-comparisons trap. Measured against simulated pure-noise price series
# (5 years, 0.4% daily vol, 40 runs) alongside a planted +6%/month pattern:
#
#   t>=1.5, median>=2%  ->  32% of NOISE called seasonal   (100% detection)
#   t>=2.5, median>=2%  ->  12%                            (100%)
#   t>=3.0, median>=3%  ->   0%                            (100%)
#
# The last row is the operating point. Erring towards "no pattern" is the right
# asymmetry: a missed seasonal edge costs an opportunity, an invented one costs
# money.
STRONG_WIN_RATE = 80.0  # % of years the month must go the same way
STRONG_ABS_MEAN = 3.0   # ...its MEDIAN move must be at least this big
MIN_T_STAT = 3.0        # ...and the average must be large next to its scatter


def _prices(symbol: str, years: int) -> pd.Series:
    """Daily closes, tz-naive, from the broker or the free fallback."""
    sym = (symbol or "").upper().replace(".NS", "").replace(".BO", "")
    lookback = int(years * 365.25) + 30
    df = pd.DataFrame()
    try:
        from src.data import MarketData
        df = MarketData().daily(f"{sym}.NS", None, lookback_days=lookback)
    except Exception as e:
        log.debug(f"broker history failed for {sym}: {e}")
    if df is None or df.empty:
        try:
            from src.data import yahoo
            df = yahoo.daily(f"{sym}.NS", lookback)
        except Exception as e:
            log.debug(f"yahoo history failed for {sym}: {e}")
    if df is None or df.empty or "close" not in df:
        return pd.Series(dtype=float)

    s = pd.to_numeric(df["close"], errors="coerce").dropna()
    idx = pd.to_datetime(s.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("Asia/Kolkata").tz_localize(None)
    s.index = idx.normalize()
    return s[~s.index.duplicated(keep="last")].sort_index()


def _monthly_returns(prices: pd.Series) -> pd.DataFrame:
    """One row per calendar month: year, month, return %.

    Grouped on an integer YYYYMM key rather than resample() — the month-end
    alias changed name in pandas 2.2 and the phone ships 2.1.3.
    """
    if prices.empty:
        return pd.DataFrame()
    df = pd.DataFrame({"close": prices})
    df["year"] = df.index.year
    df["month"] = df.index.month
    df["_ym"] = df["year"] * 100 + df["month"]
    month_end = df.groupby("_ym").last().sort_index()
    month_end["ret"] = month_end["close"].pct_change(fill_method=None) * 100
    out = month_end.dropna(subset=["ret"]).reset_index()
    return out[["_ym", "year", "month", "ret"]]


def seasonality(symbol: str, years: int = 5) -> dict:
    """Average behaviour per calendar month, with its reliability attached."""
    symbol = (symbol or "").upper().replace(".NS", "").replace(".BO", "")
    years = max(2, min(int(years or 5), 10))

    prices = _prices(symbol, years)
    if prices.empty:
        return {"symbol": symbol, "error":
                "No price history available for this stock right now."}

    cutoff = pd.Timestamp(date.today()) - pd.DateOffset(years=years)
    monthly = _monthly_returns(prices[prices.index >= cutoff])
    if monthly.empty or len(monthly) < 12:
        return {"symbol": symbol, "error":
                f"Only {len(monthly)} months of history — too little to say "
                f"anything about seasonality."}

    span_years = round(len(monthly) / 12, 1)
    rows = []
    for m in range(1, 13):
        vals = monthly.loc[monthly["month"] == m, "ret"].to_numpy()
        n = len(vals)
        if n == 0:
            rows.append({"month": m, "label": MONTHS[m - 1], "n": 0,
                         "avg": None, "median": None, "win_rate": None,
                         "best": None, "worst": None, "t_stat": None})
            continue
        mean = float(np.mean(vals))
        sd = float(np.std(vals, ddof=1)) if n > 1 else 0.0
        # t = mean / standard error. Reported so a big average backed by two
        # wild years can be told apart from a steady one.
        t = (mean / (sd / math.sqrt(n))) if (n > 1 and sd > 1e-9) else None
        rows.append({
            "month": m, "label": MONTHS[m - 1], "n": n,
            "avg": round(mean, 2),
            "median": round(float(np.median(vals)), 2),
            "win_rate": round(float((vals > 0).sum()) / n * 100, 1),
            "best": round(float(np.max(vals)), 2),
            "worst": round(float(np.min(vals)), 2),
            "std": round(sd, 2),
            "t_stat": round(t, 2) if t is not None else None,
        })

    scored = [r for r in rows if r["n"] >= 2 and r["avg"] is not None]
    best = max(scored, key=lambda r: r["avg"], default=None)
    worst = min(scored, key=lambda r: r["avg"], default=None)
    spread = round(best["avg"] - worst["avg"], 2) if best and worst else None

    # A month only counts as a "standout" if it clears three gates. Any one or
    # two of them alone is what noise looks like:
    #
    #   size        — judged on the MEDIAN, not the mean. A single +40% year
    #                 among four flat ones lifts the mean to +8% while the
    #                 median stays at +0.1%, and that is an outlier rather than
    #                 a pattern. (A test caught exactly this case.)
    #   consistency — the month went the same way in most years.
    #   signal      — the average is large relative to its own scatter.
    standouts = [
        r for r in scored
        if r["n"] >= 3
        and abs(r["median"]) >= STRONG_ABS_MEAN
        and (r["win_rate"] >= STRONG_WIN_RATE or r["win_rate"] <= 100 - STRONG_WIN_RATE)
        and (r["t_stat"] is None or abs(r["t_stat"]) >= MIN_T_STAT)
    ]

    enough = span_years >= MIN_YEARS
    if not enough:
        verdict = "not enough history"
        reading = (f"Only {span_years} years here. Monthly patterns need more "
                   f"than that before they mean anything.")
    elif not standouts:
        verdict = "no clear seasonality"
        # A bare "no" throws away what was measured. Name the strongest month
        # and why it didn't clear the bar, so the numbers can still be judged.
        if best and best.get("t_stat") is not None:
            reading = (
                f"No month clears the bar. The strongest is {best['label']} "
                f"(typically {best['median']:+.1f}%, positive in "
                f"{best['win_rate']:.0f}% of years), but with only "
                f"{best['n']} observations that is not enough to separate from "
                f"chance — across twelve months, one will always look good. "
                f"Treat the chart as description, not a signal.")
        else:
            reading = ("No month stands out consistently. Differences between "
                       "months look like normal variation.")
    else:
        up = [r["label"] for r in standouts if r["avg"] > 0]
        down = [r["label"] for r in standouts if r["avg"] < 0]
        bits = []
        if up:
            bits.append("stronger in " + ", ".join(up))
        if down:
            bits.append("weaker in " + ", ".join(down))
        verdict = "possible seasonality"
        reading = ("Historically " + " and ".join(bits) + ". "
                   f"Based on about {int(span_years)} observations per month, "
                   "which is few — treat it as a hint, not a signal.")

    return {
        "symbol": symbol,
        "years_requested": years,
        "years_covered": span_years,
        "months_observed": int(len(monthly)),
        "observations_per_month": int(round(len(monthly) / 12)),
        "by_month": rows,
        "best_month": best,
        "worst_month": worst,
        "spread_pct": spread,
        "standouts": [r["label"] for r in standouts],
        "verdict": verdict,
        "reading": reading,
        "caveat": (
            f"Each month has about {int(round(len(monthly) / 12))} observations. "
            "Twelve averages drawn from a handful of years will always show "
            "some months looking better than others — that is what randomness "
            "looks like. Win rate and the t-statistic are shown so a steady "
            "pattern can be told from one or two dramatic years."
        ),
        "by_year": _matrix(monthly),
    }


def _matrix(monthly: pd.DataFrame) -> list[dict]:
    """Year-by-month grid, so a 'pattern' driven by one year is visible."""
    out = []
    for year, grp in monthly.groupby("year"):
        row = {"year": int(year)}
        for _, r in grp.iterrows():
            row[MONTHS[int(r["month"]) - 1]] = round(float(r["ret"]), 2)
        out.append(row)
    return sorted(out, key=lambda r: -r["year"])

"""Benchmark the portfolio against a market index.

Why this module exists
----------------------
The raw ``portfolio_value`` line in the equity curve is NOT a return series:
it moves when you deposit money and buy more, not just when your stocks move.
Comparing it to NIFTY directly would flatter (or punish) you for the size of
your contributions rather than your stock picking.

So everything here is built on **time-weighted return (TWR)** — the same
measure funds are judged on. For each sampled period we strip out the external
cash flow before measuring growth::

    F_t = (cash_in_t - cash_in_{t-1}) - (proceeds_t - proceeds_{t-1})
    r_t = (V_t - F_t) / V_{t-1} - 1

and chain-link the ``r_t``. That number is directly comparable to an index.

Caveats, stated rather than hidden:
  * The equity curve is sampled ~weekly, so flows are treated as arriving at
    the END of each period (simple Dietz). Intra-period timing is ignored.
  * A period where the book was empty (``V_{t-1} <= 0``) contributes no return.
  * Index data comes from the free Yahoo chart endpoint (price index, so it
    excludes dividends — as does the portfolio value line, keeping it fair).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from src.utils.logger import get_logger

log = get_logger("portfolio.benchmark")

DEFAULT_BENCHMARK = "^NSEI"

BENCHMARK_NAMES: dict[str, str] = {
    "^NSEI": "NIFTY 50",
    "^BSESN": "SENSEX",
    "^NSEBANK": "NIFTY BANK",
    "^CNX100": "NIFTY 100",
    "^CNXIT": "NIFTY IT",
}


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

def twr_series(curve: pd.DataFrame) -> pd.DataFrame:
    """Chain-linked time-weighted return index (base 100) from an equity curve.

    Needs ``date``, ``portfolio_value`` and — to remove deposits/withdrawals —
    ``cash_in`` and ``proceeds``. If those flow columns are missing (an older
    cached run), we fall back to raw value growth and flag it to the caller via
    :func:`compare`'s ``flows_available``.

    Returns columns: ``date``, ``value``, ``period_return``, ``twr``.
    """
    if curve is None or curve.empty or len(curve) < 2:
        return pd.DataFrame()

    ec = curve.copy()
    ec["date"] = pd.to_datetime(ec["date"])
    ec = ec.sort_values("date").reset_index(drop=True)

    has_flows = "cash_in" in ec.columns and "proceeds" in ec.columns
    v = pd.to_numeric(ec["portfolio_value"], errors="coerce").fillna(0.0)
    if has_flows:
        cash_in = pd.to_numeric(ec["cash_in"], errors="coerce").fillna(0.0)
        proceeds = pd.to_numeric(ec["proceeds"], errors="coerce").fillna(0.0)
        flow = (cash_in.diff() - proceeds.diff()).fillna(0.0)
    else:
        flow = pd.Series(0.0, index=ec.index)

    rets = [np.nan]
    for i in range(1, len(ec)):
        prev = float(v.iloc[i - 1])
        if prev <= 0:
            rets.append(np.nan)          # book was empty — no return to measure
            continue
        r = (float(v.iloc[i]) - float(flow.iloc[i])) / prev - 1.0
        # A return outside [-100%, +1000%] in one weekly step is a data
        # artefact (a mis-stamped flow), not performance. Drop it rather than
        # let it poison the chain.
        rets.append(r if -0.999 < r < 10.0 else np.nan)

    out = pd.DataFrame({
        "date": ec["date"],
        "value": v,
        "period_return": rets,
    })
    out["twr"] = 100.0 * (1.0 + out["period_return"].fillna(0.0)).cumprod()
    return out


def index_series(symbol: str, start: date, end: date) -> pd.Series:
    """Daily closes for an index, indexed by Timestamp. Empty on failure."""
    from src.data import yahoo
    ticker = yahoo.resolve_index(symbol)
    lookback = (end - start).days + 10
    try:
        df = yahoo.daily(ticker, lookback_days=max(lookback, 30))
    except Exception as e:
        log.warning(f"index fetch failed for {ticker}: {e}")
        return pd.Series(dtype=float)
    if df is None or df.empty or "close" not in df:
        return pd.Series(dtype=float)
    s = pd.to_numeric(df["close"], errors="coerce").dropna()
    # Yahoo stamps each bar at the market OPEN in UTC (09:15 IST -> 03:45Z).
    # The equity curve's dates are plain calendar dates (midnight), so an
    # as-of join against the raw stamps would silently pick the PREVIOUS
    # session and shift the whole index leg one day. Normalise to midnight.
    s.index = pd.to_datetime(s.index).normalize()
    s = s[~s.index.duplicated(keep="last")]
    return s.sort_index()


def _asof(series: pd.Series, when: pd.Timestamp) -> Optional[float]:
    """Last observation on or before `when` (indices don't trade every day)."""
    valid = series[series.index <= when]
    return float(valid.iloc[-1]) if not valid.empty else None


def _max_drawdown(level: pd.Series) -> float:
    """Worst peak-to-trough fall of a level series, as a negative percentage."""
    lvl = level.dropna()
    if len(lvl) < 2:
        return 0.0
    dd = lvl / lvl.cummax() - 1.0
    return round(float(dd.min()) * 100, 2)


def _periods_per_year(dates: pd.Series) -> float:
    """Annualisation factor implied by the sampling interval."""
    if len(dates) < 3:
        return 52.0
    gap = pd.Series(dates).diff().dt.days.dropna()
    med = float(gap.median()) if not gap.empty else 7.0
    return 365.25 / max(med, 1.0)


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------

def compare(
    curve: pd.DataFrame,
    benchmark: str = DEFAULT_BENCHMARK,
    window_days: int = 365,
) -> dict:
    """Portfolio vs index over the trailing `window_days`.

    Returns a JSON-ready dict:
      ``series``   both rebased to 100 at the window start (the chart),
      ``rolling``  trailing-1Y return at each point, portfolio vs index,
      ``monthly``  calendar-month returns for the last 12 months,
      ``stats``    return / excess / beta / alpha / correlation / capture /
                   volatility / max drawdown,
      ``note``     what the numbers do and don't include.
    """
    tw = twr_series(curve)
    if tw.empty:
        return {"error": "Not enough portfolio history to benchmark. "
                         "Run the performance analysis first."}

    flows_available = "cash_in" in (curve.columns if curve is not None else [])
    ticker = None
    try:
        from src.data import yahoo
        ticker = yahoo.resolve_index(benchmark)
    except Exception:
        ticker = benchmark

    end_ts = tw["date"].iloc[-1]
    first_ts = tw["date"].iloc[0]
    start_ts = max(first_ts, end_ts - pd.Timedelta(days=window_days))

    idx = index_series(ticker, first_ts.date(), end_ts.date() + timedelta(days=1))
    if idx.empty:
        return {"error": f"Could not fetch {BENCHMARK_NAMES.get(ticker, ticker)} "
                         "prices right now (Yahoo unreachable). Try again."}

    # Align the index onto the portfolio's own sample dates.
    tw = tw.copy()
    tw["index_level"] = [_asof(idx, d) for d in tw["date"]]
    tw = tw.dropna(subset=["index_level"]).reset_index(drop=True)
    if len(tw) < 3:
        return {"error": "Portfolio history and index prices barely overlap — "
                         "need at least a few weeks of both."}

    win = tw[tw["date"] >= start_ts].reset_index(drop=True)
    if len(win) < 3:
        win = tw.copy()

    # ---- rebase both to 100 at the window start (the headline chart) ----
    p0 = float(win["twr"].iloc[0]) or 1.0
    i0 = float(win["index_level"].iloc[0]) or 1.0
    series = [
        {
            "date": d.strftime("%Y-%m-%d"),
            "portfolio": round(float(p) / p0 * 100, 2),
            "index": round(float(i) / i0 * 100, 2),
        }
        for d, p, i in zip(win["date"], win["twr"], win["index_level"])
    ]

    # ---- period returns, for the regression stats ----
    pr = win["twr"].pct_change()
    ir = win["index_level"].pct_change()
    both = pd.DataFrame({"p": pr, "i": ir}).dropna()

    stats: dict = {}
    ppy = _periods_per_year(win["date"])

    port_total = float(win["twr"].iloc[-1]) / p0 - 1.0
    idx_total = float(win["index_level"].iloc[-1]) / i0 - 1.0
    span_years = max((win["date"].iloc[-1] - win["date"].iloc[0]).days / 365.25, 1e-6)

    stats["portfolio_return_pct"] = round(port_total * 100, 2)
    stats["index_return_pct"] = round(idx_total * 100, 2)
    stats["excess_pct"] = round((port_total - idx_total) * 100, 2)
    stats["span_years"] = round(span_years, 2)
    stats["periods"] = int(len(both))

    if len(both) >= 4:
        pv, iv = both["p"].to_numpy(), both["i"].to_numpy()
        var_i = float(np.var(iv, ddof=1))
        beta = float(np.cov(pv, iv, ddof=1)[0][1] / var_i) if var_i > 1e-12 else None
        stats["beta"] = round(beta, 2) if beta is not None else None
        # Annualised Jensen's alpha, risk-free ignored (both legs share it).
        if beta is not None:
            per_alpha = float(np.mean(pv) - beta * np.mean(iv))
            stats["alpha_pct"] = round(((1 + per_alpha) ** ppy - 1) * 100, 2)
        corr = float(np.corrcoef(pv, iv)[0][1]) if var_i > 1e-12 else None
        stats["correlation"] = round(corr, 2) if corr is not None and np.isfinite(corr) else None
        stats["tracking_error_pct"] = round(
            float(np.std(pv - iv, ddof=1)) * np.sqrt(ppy) * 100, 2)
        stats["portfolio_vol_pct"] = round(float(np.std(pv, ddof=1)) * np.sqrt(ppy) * 100, 2)
        stats["index_vol_pct"] = round(float(np.std(iv, ddof=1)) * np.sqrt(ppy) * 100, 2)

        # Capture ratios: how much of the index's up / down moves you caught.
        up, dn = iv > 0, iv < 0
        if up.sum() >= 2 and abs(iv[up].sum()) > 1e-9:
            stats["up_capture_pct"] = round(pv[up].sum() / iv[up].sum() * 100, 1)
        if dn.sum() >= 2 and abs(iv[dn].sum()) > 1e-9:
            stats["down_capture_pct"] = round(pv[dn].sum() / iv[dn].sum() * 100, 1)

    stats["portfolio_max_drawdown_pct"] = _max_drawdown(win["twr"])
    stats["index_max_drawdown_pct"] = _max_drawdown(win["index_level"])

    # ---- trailing-1Y rolling return, at every sample point ----
    rolling = []
    for i in range(len(tw)):
        d = tw["date"].iloc[i]
        if d < start_ts:
            continue
        past = tw[tw["date"] <= d - pd.Timedelta(days=365)]
        if past.empty:
            continue
        pp, pi = float(past["twr"].iloc[-1]), float(past["index_level"].iloc[-1])
        if pp <= 0 or pi <= 0:
            continue
        rolling.append({
            "date": d.strftime("%Y-%m-%d"),
            "portfolio_pct": round((float(tw["twr"].iloc[i]) / pp - 1) * 100, 2),
            "index_pct": round((float(tw["index_level"].iloc[i]) / pi - 1) * 100, 2),
        })

    # ---- calendar-month returns for the last 12 months ----
    m = tw.set_index("date")[["twr", "index_level"]].resample("ME").last().dropna()
    monthly = []
    if len(m) >= 2:
        mp = m["twr"].pct_change().dropna()
        mi = m["index_level"].pct_change().dropna()
        for ts in mp.index[-12:]:
            if ts not in mi.index:
                continue
            monthly.append({
                "month": ts.strftime("%b %y"),
                "portfolio_pct": round(float(mp.loc[ts]) * 100, 2),
                "index_pct": round(float(mi.loc[ts]) * 100, 2),
            })

    note = (
        "Portfolio performance is time-weighted, so deposits and withdrawals "
        "don't count as gains — it measures your stock picking, exactly like "
        "the index. Both legs are price-only (dividends excluded on each side)."
    )
    if not flows_available:
        note = ("This run predates cash-flow tracking, so returns include the "
                "effect of new money. Re-run the performance analysis for a "
                "true time-weighted comparison. ") + note

    return {
        "benchmark": {"symbol": ticker,
                      "name": BENCHMARK_NAMES.get(ticker, ticker)},
        "window_days": window_days,
        "start": win["date"].iloc[0].strftime("%Y-%m-%d"),
        "end": win["date"].iloc[-1].strftime("%Y-%m-%d"),
        "flows_available": bool(flows_available),
        "series": series,
        "rolling": rolling,
        "rolling_available": len(rolling) >= 2,
        "monthly": monthly,
        "stats": stats,
        "note": note,
    }

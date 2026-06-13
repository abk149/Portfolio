"""Modern Portfolio Theory optimizer.

Given a set of tickers (current holdings, or a custom list — e.g. the screener's
buy list), pull historical daily returns and solve for:

  - max_sharpe       : maximum Sharpe-ratio portfolio (return per unit risk)
  - min_variance     : minimum-variance portfolio
  - target_return    : minimum variance subject to a target annual return
  - efficient_frontier : sample of (risk, return) points along the frontier

Constraints:
  - long-only (weights >= 0)
  - fully invested (sum to 1)
  - optional per-name cap (default 25%) to prevent corner solutions

Returns annualised statistics assuming 252 trading days.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np
import pandas as pd
from src.utils.compat import minimize

from src.data import MarketData
from src.upstox.client import UpstoxClient
from src.utils.logger import get_logger

log = get_logger("optimizer")

TRADING_DAYS = 252


@dataclass
class OptResult:
    weights: pd.Series
    expected_return: float       # annualised
    volatility: float            # annualised
    sharpe: float
    risk_free: float
    extras: dict


def _annualise(daily_returns: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    mu = daily_returns.mean() * TRADING_DAYS
    cov = daily_returns.cov() * TRADING_DAYS
    return mu, cov


def _stats(w, mu, cov, rf):
    ret = float(w @ mu)
    vol = float(np.sqrt(w @ cov.values @ w))
    sharpe = (ret - rf) / vol if vol > 0 else 0.0
    return ret, vol, sharpe


def _solve(mu, cov, rf, objective, target_return=None, max_weight=0.25):
    """SLSQP solve. Tries multiple starting points to avoid SLSQP's notorious
    'Positive directional derivative for linesearch' false negatives."""
    n = len(mu)
    # Feasibility check: if max_weight is too tight (e.g. 0.25 with 3 assets),
    # we can't even sum to 1. Auto-relax just enough.
    min_feasible = 1.0 / n
    if max_weight < min_feasible + 1e-6:
        max_weight = min(1.0, min_feasible + 0.05)

    bounds = [(0.0, max_weight)] * n
    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
    if target_return is not None:
        # Clamp target into the achievable range so SLSQP has a feasible region
        target_return = float(np.clip(target_return, float(mu.min()), float(mu.max())))
        cons.append({"type": "eq", "fun": lambda w: float(w @ mu) - target_return})

    # Multiple starting points — first equal weights, then a few biased seeds
    starts = [np.repeat(1 / n, n)]
    if target_return is None:
        # bias toward higher mu
        ranks = mu.values.argsort()[::-1]
        w = np.zeros(n); w[ranks[: max(1, n // 3)]] = 1.0; starts.append(w / w.sum())
    rng = np.random.default_rng(7)
    for _ in range(3):
        rs = rng.random(n)
        starts.append(rs / rs.sum())

    best = None
    for w0 in starts:
        w0 = np.clip(w0, 0.0, max_weight)
        if w0.sum() > 0:
            w0 = w0 / w0.sum()
        res = minimize(
            objective, w0, args=(mu, cov, rf), method="SLSQP",
            bounds=bounds, constraints=cons,
            options={"ftol": 1e-7, "maxiter": 1000},
        )
        if res.success:
            return res.x
        if best is None or res.fun < best.fun:
            best = res
    log.debug(f"optimizer fell back to best non-converged solution: {best.message}")
    return best.x


def _neg_sharpe(w, mu, cov, rf):
    ret, vol, _ = _stats(w, mu, cov, rf)
    return -((ret - rf) / vol) if vol > 0 else 1e6


def _variance(w, mu, cov, rf):
    return float(w @ cov.values @ w)


class PortfolioOptimizer:
    def __init__(self, upstox: Optional[UpstoxClient] = None, risk_free: float = 0.07):
        try:
            self.upstox = upstox or UpstoxClient()
        except Exception:
            self.upstox = None
        self.md = MarketData(self.upstox)
        self.rf = risk_free  # India ~7% 10y G-sec

    # ---------------- data ----------------
    def returns(
        self,
        tickers: list[tuple[str, str]],   # (yf_ticker, instrument_key) — ikey may be ""/None
        lookback_days: int = 365,
    ) -> pd.DataFrame:
        """Pull close prices for each ticker. Auto-resolves instrument_keys when
        missing so Upstox is used wherever possible. yfinance is the fallback."""
        from src.data.instruments import resolve_instrument_key
        series = {}
        for yf_t, ikey in tickers:
            if not ikey:
                ikey = resolve_instrument_key(yf_t)
            try:
                df = self.md.daily(yf_t, ikey, lookback_days=lookback_days)
                if df is not None and not df.empty:
                    series[yf_t] = df["close"]
            except Exception as e:
                log.debug(f"return load failed {yf_t}: {e}")
        if not series:
            return pd.DataFrame()
        prices = pd.concat(series, axis=1).dropna(how="any")
        return prices.pct_change().dropna()

    # ---------------- optimizations ----------------
    def max_sharpe(self, daily_returns: pd.DataFrame, max_weight: float = 0.25) -> OptResult:
        mu, cov = _annualise(daily_returns)
        w = _solve(mu, cov, self.rf, _neg_sharpe, max_weight=max_weight)
        return self._wrap(w, mu, cov)

    def min_variance(self, daily_returns: pd.DataFrame, max_weight: float = 0.25) -> OptResult:
        mu, cov = _annualise(daily_returns)
        w = _solve(mu, cov, self.rf, _variance, max_weight=max_weight)
        return self._wrap(w, mu, cov)

    def target_return(self, daily_returns: pd.DataFrame, target: float,
                      max_weight: float = 0.25) -> OptResult:
        mu, cov = _annualise(daily_returns)
        w = _solve(mu, cov, self.rf, _variance, target_return=target, max_weight=max_weight)
        return self._wrap(w, mu, cov)

    def efficient_frontier(self, daily_returns: pd.DataFrame, points: int = 25,
                           max_weight: float = 0.25) -> pd.DataFrame:
        mu, cov = _annualise(daily_returns)
        lo, hi = float(mu.min()), float(mu.max())
        targets = np.linspace(lo, hi, points)
        rows = []
        for t in targets:
            try:
                w = _solve(mu, cov, self.rf, _variance, target_return=t, max_weight=max_weight)
                r, v, s = _stats(w, mu, cov, self.rf)
                rows.append({"target": t, "return": r, "vol": v, "sharpe": s})
            except Exception:
                continue
        return pd.DataFrame(rows)

    def _wrap(self, w: np.ndarray, mu: pd.Series, cov: pd.DataFrame) -> OptResult:
        weights = pd.Series(w, index=mu.index).round(4)
        weights = weights[weights > 1e-4].sort_values(ascending=False)
        weights = weights / weights.sum()
        ret, vol, sharpe = _stats(weights.reindex(mu.index, fill_value=0).values, mu, cov, self.rf)
        return OptResult(
            weights=weights, expected_return=ret, volatility=vol,
            sharpe=sharpe, risk_free=self.rf,
            extras={"per_name_return": mu.round(4).to_dict()},
        )

    # ---------------- cash-deployment optimizer ----------------
    def deploy_cash(
        self,
        current_value_by_yf: dict[str, float],
        cash_to_deploy: float,
        candidates_extra: list[str] | None = None,
        max_weight: float = 0.25,
        lookback_days: int = 365,
    ) -> dict:
        """Given existing INR positions + a new cash amount, return the
        BUY-ONLY allocation that maximises the resulting portfolio's Sharpe.

        Constraints:
          • adds_i ≥ 0           (no selling — preserves tax basis)
          • Σ adds_i = cash      (full budget gets deployed)
          • final weight_i ≤ max_weight  (no concentration)

        `candidates_extra` lets you include new tickers not yet owned —
        typically the STRONG_BUY rows from the universe map.
        """
        # Build the working ticker set: current holdings + optional new candidates
        cur = {k: float(v) for k, v in current_value_by_yf.items() if v > 0}
        tickers = list(cur.keys())
        for t in (candidates_extra or []):
            if t not in cur:
                tickers.append(t)
                cur[t] = 0.0
        if not tickers:
            return {"error": "no tickers to consider"}

        rets = self.returns([(t, None) for t in tickers], lookback_days=lookback_days)
        if rets.empty or rets.shape[1] < 2:
            return {"error": "insufficient overlapping return history"}

        cols = list(rets.columns)
        cur_arr = np.array([cur.get(t, 0.0) for t in cols], dtype=float)
        mu, cov = _annualise(rets)
        total_after = cur_arr.sum() + cash_to_deploy
        cov_v = cov.values
        mu_v = mu.values
        n = len(cols)

        def neg_sharpe(adds):
            w = (cur_arr + adds) / total_after
            ret = float(w @ mu_v)
            vol = float(np.sqrt(w @ cov_v @ w))
            return -((ret - self.rf) / vol) if vol > 0 else 1e6

        cons = [
            {"type": "eq",   "fun": lambda x: x.sum() - cash_to_deploy},
            {"type": "ineq", "fun": lambda x: cash_to_deploy * max_weight
                                              - ((cur_arr + x) / total_after).max()
                                              * total_after + cur_arr.max()},
        ]
        # Per-name upper bound — final weight cap
        bounds = [(0.0, max(0.0, max_weight * total_after - cur_arr[i]))
                  for i in range(n)]
        x0 = np.repeat(cash_to_deploy / n, n)

        best = None
        for seed in range(5):
            x = x0 if seed == 0 else np.random.default_rng(seed).dirichlet(
                np.ones(n)) * cash_to_deploy
            r = minimize(neg_sharpe, x, method="SLSQP",
                         bounds=bounds, constraints=cons,
                         options={"ftol": 1e-7, "maxiter": 800})
            if r.success and (best is None or r.fun < best.fun):
                best = r
        if best is None:
            return {"error": "optimization failed to converge"}

        adds = np.clip(best.x, 0, None)
        if adds.sum() > 0:
            adds = adds * (cash_to_deploy / adds.sum())   # exact budget

        final_w = (cur_arr + adds) / total_after
        f_ret = float(final_w @ mu_v)
        f_vol = float(np.sqrt(final_w @ cov_v @ final_w))
        f_sharpe = (f_ret - self.rf) / f_vol if f_vol > 0 else 0.0

        if cur_arr.sum() > 0:
            cw = cur_arr / cur_arr.sum()
            c_ret = float(cw @ mu_v)
            c_vol = float(np.sqrt(cw @ cov_v @ cw))
            c_sharpe = (c_ret - self.rf) / c_vol if c_vol > 0 else 0.0
        else:
            c_ret = c_vol = c_sharpe = 0.0

        buys = []
        for i, t in enumerate(cols):
            if adds[i] >= 1:        # ignore < ₹1 noise
                buys.append({
                    "ticker": t,
                    "buy_inr": round(float(adds[i]), 0),
                    "current_inr": round(float(cur_arr[i]), 0),
                    "final_weight_pct": round(float(final_w[i]) * 100, 2),
                    "is_new_position": cur_arr[i] == 0,
                })
        buys.sort(key=lambda b: b["buy_inr"], reverse=True)

        return {
            "cash_to_deploy": cash_to_deploy,
            "buys": buys,
            "before": {
                "return_pct": round(c_ret * 100, 2),
                "vol_pct":    round(c_vol * 100, 2),
                "sharpe":     round(c_sharpe, 3),
                "invested_inr": round(float(cur_arr.sum()), 0),
            },
            "after": {
                "return_pct": round(f_ret * 100, 2),
                "vol_pct":    round(f_vol * 100, 2),
                "sharpe":     round(f_sharpe, 3),
                "invested_inr": round(float(total_after), 0),
            },
            "sharpe_uplift": round(f_sharpe - c_sharpe, 3),
        }

    # ---------------- portfolio-aware re-allocation ----------------
    def rebalance_suggestion(
        self,
        current_value_by_yf: dict[str, float],
        target: OptResult,
    ) -> pd.DataFrame:
        """Compare current INR allocation to MPT-optimal weights."""
        total = sum(current_value_by_yf.values())
        if total <= 0:
            return pd.DataFrame()
        cur = pd.Series(current_value_by_yf) / total
        all_idx = cur.index.union(target.weights.index)
        df = pd.DataFrame({
            "current_pct": (cur.reindex(all_idx).fillna(0) * 100).round(2),
            "target_pct":  (target.weights.reindex(all_idx).fillna(0) * 100).round(2),
        })
        df["delta_pct"] = (df["target_pct"] - df["current_pct"]).round(2)
        df["delta_inr"] = (df["delta_pct"] / 100 * total).round(0)
        df["action"] = df["delta_inr"].apply(lambda x: "BUY" if x > 0 else ("SELL" if x < 0 else "HOLD"))
        return df.sort_values("delta_inr", key=lambda s: s.abs(), ascending=False)

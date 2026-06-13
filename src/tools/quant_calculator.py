"""Thin tool facade around PortfolioOptimizer, exposed to the agent.

The optimiser itself lives in src/portfolio/optimizer.py — this wrapper just
gives the agent a stable, JSON-friendly interface.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from src.data import MarketData
from src.portfolio import PortfolioOptimizer
from src.upstox.client import UpstoxClient
from src.brokers import get_broker


class QuantCalculator:
    def __init__(self, upstox: Optional[UpstoxClient] = None):
        try:
            self.up = upstox or get_broker()
        except Exception:
            self.up = None
        self.md = MarketData(self.up)
        self.opt = PortfolioOptimizer(self.up)

    def _returns(self, yf_tickers: list[str], lookback: int = 365) -> pd.DataFrame:
        pairs = [(t, None) for t in yf_tickers]
        return self.opt.returns(pairs, lookback_days=lookback)

    def max_sharpe(self, yf_tickers: list[str], max_weight: float = 0.25) -> dict:
        rets = self._returns(yf_tickers)
        if rets.empty or rets.shape[1] < 2:
            return {"error": "insufficient overlapping history"}
        r = self.opt.max_sharpe(rets, max_weight=max_weight)
        return {
            "expected_return_pct": round(r.expected_return * 100, 2),
            "volatility_pct": round(r.volatility * 100, 2),
            "sharpe": round(r.sharpe, 3),
            "weights_pct": (r.weights * 100).round(2).to_dict(),
        }

    def efficient_frontier(self, yf_tickers: list[str], points: int = 15) -> list[dict]:
        rets = self._returns(yf_tickers)
        if rets.empty:
            return []
        df = self.opt.efficient_frontier(rets, points=points)
        return df.round(4).to_dict("records")

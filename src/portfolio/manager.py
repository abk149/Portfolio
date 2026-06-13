"""Portfolio aggregation and analytics."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from src.upstox.client import UpstoxClient
from src.brokers import get_broker


@dataclass
class PortfolioSnapshot:
    holdings: pd.DataFrame      # long-term holdings
    positions: pd.DataFrame     # intraday/short-term positions
    summary: dict               # totals
    allocation: pd.DataFrame    # by sector/industry if available


class PortfolioManager:
    def __init__(self, upstox: Optional[UpstoxClient] = None):
        self.upstox = upstox or get_broker()

    def snapshot(self) -> PortfolioSnapshot:
        h = pd.DataFrame(self.upstox.holdings())
        p = pd.DataFrame(self.upstox.positions())

        if not h.empty:
            h["invested"] = h["quantity"] * h["average_price"]
            h["current_value"] = h["quantity"] * h["last_price"]
            h["pnl"] = h["current_value"] - h["invested"]
            h["pnl_pct"] = (h["pnl"] / h["invested"]).round(4) * 100
            h["day_change_value"] = h["quantity"] * h.get("day_change", 0)

        if not p.empty:
            p["pnl"] = p.get("pnl", 0)
            p["mtm"] = p.get("unrealised", 0) + p.get("realised", 0)

        summary = {
            "holdings_invested": float(h["invested"].sum()) if not h.empty else 0,
            "holdings_value": float(h["current_value"].sum()) if not h.empty else 0,
            "holdings_pnl": float(h["pnl"].sum()) if not h.empty else 0,
            "holdings_pnl_pct": (
                100 * float(h["pnl"].sum()) / float(h["invested"].sum())
                if not h.empty and h["invested"].sum() else 0.0
            ),
            "day_change_value": float(h["day_change_value"].sum()) if not h.empty else 0,
            "positions_pnl": float(p["pnl"].sum()) if not p.empty else 0,
            "n_holdings": len(h),
            "n_positions": len(p),
        }

        alloc = pd.DataFrame()
        if not h.empty:
            grp = "sector" if "sector" in h.columns else "tradingsymbol"
            alloc = (
                h.groupby(grp)["current_value"]
                .sum()
                .sort_values(ascending=False)
                .reset_index()
            )
            alloc["pct"] = (alloc["current_value"] / alloc["current_value"].sum() * 100).round(2)

        return PortfolioSnapshot(h, p, summary, alloc)

    def concentration_risk(self, snap: PortfolioSnapshot, threshold_pct: float = 15.0) -> pd.DataFrame:
        """Flag any single holding > threshold_pct of total."""
        if snap.holdings.empty:
            return pd.DataFrame()
        h = snap.holdings.copy()
        total = h["current_value"].sum()
        h["weight_pct"] = (h["current_value"] / total * 100).round(2)
        return h[h["weight_pct"] > threshold_pct][["tradingsymbol", "weight_pct", "current_value", "pnl_pct"]]

    def underperformers(self, snap: PortfolioSnapshot, pnl_threshold: float = -10.0) -> pd.DataFrame:
        if snap.holdings.empty:
            return pd.DataFrame()
        h = snap.holdings
        return h[h["pnl_pct"] < pnl_threshold][
            ["tradingsymbol", "quantity", "average_price", "last_price", "pnl", "pnl_pct"]
        ].sort_values("pnl_pct")

"""Analyze the user's historical intraday trades to surface patterns and mistakes."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from src.upstox.client import UpstoxClient
from src.brokers import get_broker


class IntradayAnalyzer:
    def __init__(self, upstox: Optional[UpstoxClient] = None):
        self.upstox = upstox or get_broker()

    def _fetch(self, days: int) -> pd.DataFrame:
        end = date.today()
        start = end - timedelta(days=days)
        # Upstox enforces single FY → chunk by 350-day windows
        rows: list[dict] = []
        cur = start
        while cur < end:
            nxt = min(cur + timedelta(days=350), end)
            try:
                rows.extend(self.upstox.trade_history(cur, nxt) or [])
            except Exception:
                pass
            cur = nxt + timedelta(days=1)
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df["trade_date"] = pd.to_datetime(df.get("trade_date") or df.get("order_timestamp")).dt.date
        df["qty"] = df["quantity"].astype(float)
        df["price"] = df["price"].astype(float)
        df["signed_qty"] = np.where(df["transaction_type"] == "BUY", df["qty"], -df["qty"])
        df["cashflow"] = -df["signed_qty"] * df["price"]
        return df

    def _close_out_intraday(self, df: pd.DataFrame) -> pd.DataFrame:
        """Pair BUY/SELL within same symbol+date → realised round-trips."""
        results = []
        for (sym, d), g in df.groupby(["tradingsymbol", "trade_date"]):
            net_qty = g["signed_qty"].sum()
            if abs(net_qty) > 1e-6:
                continue  # carry-forward, treat as positional
            cashflow = g["cashflow"].sum()
            gross_qty = g.loc[g["signed_qty"] > 0, "qty"].sum()
            avg_buy = (g.loc[g["signed_qty"] > 0, "qty"] * g.loc[g["signed_qty"] > 0, "price"]).sum() / max(gross_qty, 1e-9)
            avg_sell = (g.loc[g["signed_qty"] < 0, "qty"] * g.loc[g["signed_qty"] < 0, "price"]).sum() / max(gross_qty, 1e-9)
            results.append({
                "date": d, "symbol": sym, "qty": gross_qty,
                "avg_buy": round(avg_buy, 2), "avg_sell": round(avg_sell, 2),
                "pnl": round(cashflow, 2),
                "pnl_pct": round((avg_sell - avg_buy) / avg_buy * 100, 2) if avg_buy else 0,
                "n_legs": len(g),
                "dow": pd.Timestamp(d).day_name(),
            })
        return pd.DataFrame(results)

    def analyze(self, days: int = 90) -> dict:
        raw = self._fetch(days)
        if raw.empty:
            return {"error": "No trades returned by Upstox for the requested window."}
        rt = self._close_out_intraday(raw)
        if rt.empty:
            return {"error": "No closed intraday round-trips in window.", "raw_trades": len(raw)}

        wins = rt[rt["pnl"] > 0]
        losses = rt[rt["pnl"] <= 0]

        stats = {
            "window_days": days,
            "trades": int(len(rt)),
            "win_rate_pct": round(len(wins) / len(rt) * 100, 2),
            "avg_win": round(wins["pnl"].mean(), 2) if not wins.empty else 0,
            "avg_loss": round(losses["pnl"].mean(), 2) if not losses.empty else 0,
            "expectancy": round(rt["pnl"].mean(), 2),
            "profit_factor": round(wins["pnl"].sum() / abs(losses["pnl"].sum()), 2) if not losses.empty and losses["pnl"].sum() != 0 else None,
            "total_pnl": round(rt["pnl"].sum(), 2),
            "best": rt.nlargest(5, "pnl")[["date", "symbol", "pnl", "pnl_pct"]].to_dict("records"),
            "worst": rt.nsmallest(5, "pnl")[["date", "symbol", "pnl", "pnl_pct"]].to_dict("records"),
            "by_symbol": rt.groupby("symbol")["pnl"].agg(["count", "sum", "mean"]).sort_values("sum", ascending=False).head(15).reset_index().to_dict("records"),
            "by_dow": rt.groupby("dow")["pnl"].agg(["count", "sum", "mean"]).reset_index().to_dict("records"),
        }

        mistakes = []
        if stats["profit_factor"] and stats["profit_factor"] < 1:
            mistakes.append("Losing system overall — profit factor below 1.")
        if stats["avg_loss"] and abs(stats["avg_loss"]) > 1.5 * (stats["avg_win"] or 1):
            mistakes.append("Average loss is materially larger than average win — tighten stops or scale winners.")
        if stats["win_rate_pct"] < 35 and (stats["profit_factor"] or 0) < 1.3:
            mistakes.append("Low win-rate without a strong payoff ratio — consider mean-reversion vs breakout fit.")
        losers_by_sym = [r for r in stats["by_symbol"] if r["sum"] < 0]
        if losers_by_sym:
            mistakes.append(f"Persistent losers: {', '.join(r['symbol'] for r in losers_by_sym[:5])} — consider banning these symbols.")
        stats["mistakes"] = mistakes
        return stats

from __future__ import annotations

from src.agents.base import BaseAgent
from src.portfolio import PortfolioManager, PortfolioOptimizer, ReportBuilder


class PortfolioAgent(BaseAgent):
    SYSTEM = (
        "You are a portfolio analyst for an Indian retail trader with an Upstox account. "
        "Use the tools to read holdings, positions, and exposure. "
        "When the user asks about optimization or 'best return per risk', call the MPT "
        "optimizer tool (max_sharpe or target_return) and explain the suggested "
        "reallocation in concrete INR moves. Always cite numbers. Flag concentration "
        "risk, underperformers, and tax-loss harvesting candidates. Be concise."
    )

    TOOLS = [
        {
            "name": "get_portfolio_snapshot",
            "description": "Fetch live holdings, positions, summary KPIs, and allocation breakdown.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "concentration_risk",
            "description": "List holdings exceeding a weight % of the total portfolio.",
            "input_schema": {"type": "object",
                "properties": {"threshold_pct": {"type": "number", "default": 15}}},
        },
        {
            "name": "underperformers",
            "description": "List holdings below a P&L % threshold (e.g. -10).",
            "input_schema": {"type": "object",
                "properties": {"pnl_threshold": {"type": "number", "default": -10}}},
        },
        {
            "name": "optimize_max_sharpe",
            "description": "Run MPT max-Sharpe optimization across the user's current holdings "
                           "and suggest a rebalance. Returns target weights, expected annual "
                           "return, volatility, Sharpe, and per-name BUY/SELL deltas in INR.",
            "input_schema": {"type": "object", "properties": {
                "max_weight": {"type": "number", "default": 0.25},
                "lookback_days": {"type": "integer", "default": 365}}},
        },
        {
            "name": "optimize_target_return",
            "description": "MPT min-variance subject to target annual return (e.g. 0.18 for 18%).",
            "input_schema": {"type": "object", "properties": {
                "target_return": {"type": "number"},
                "max_weight": {"type": "number", "default": 0.25}},
                "required": ["target_return"]},
        },
        {
            "name": "generate_report",
            "description": "Render an HTML + CSV portfolio report and return file paths.",
            "input_schema": {"type": "object", "properties": {}},
        },
    ]

    def __init__(self):
        super().__init__()
        self.pm = PortfolioManager()
        self.opt = PortfolioOptimizer()
        self._snap = None

    def _snapshot(self):
        if self._snap is None:
            self._snap = self.pm.snapshot()
        return self._snap

    # Override BaseAgent.run so we can embed a live portfolio brief in the
    # system prompt — the model gets ground truth immediately instead of
    # burning a tool call to fetch it.
    def run(self, user_message: str) -> str:
        try:
            snap = self._snapshot()
            brief_lines = [
                "LIVE PORTFOLIO BRIEF (auto-injected, you do not need to call tools "
                "to fetch this; only call tools for deeper queries):",
                f"  invested ₹{snap.summary['holdings_invested']:.0f}, "
                f"current ₹{snap.summary['holdings_value']:.0f}, "
                f"P&L ₹{snap.summary['holdings_pnl']:.0f} "
                f"({snap.summary['holdings_pnl_pct']:.2f}%)",
                f"  day change ₹{snap.summary['day_change_value']:.0f}, "
                f"{snap.summary['n_holdings']} holdings, "
                f"{snap.summary['n_positions']} positions",
            ]
            if not snap.holdings.empty:
                top = snap.holdings.nlargest(8, "current_value")
                brief_lines.append("  top holdings:")
                for _, r in top.iterrows():
                    brief_lines.append(
                        f"    • {r['tradingsymbol']} qty={r['quantity']} "
                        f"avg={r['average_price']:.0f} ltp={r['last_price']:.0f} "
                        f"P&L={r['pnl']:.0f} ({r['pnl_pct']:.1f}%)"
                    )
            live_brief = "\n".join(brief_lines)
        except Exception as e:
            live_brief = f"(live brief unavailable: {e})"

        original_system = self.SYSTEM
        self.SYSTEM = f"{original_system}\n\n{live_brief}"
        try:
            return super().run(user_message)
        finally:
            self.SYSTEM = original_system

    def _build_tickers(self):
        snap = self._snapshot()
        h = snap.holdings
        if h.empty:
            return [], {}
        tickers = []
        value_by_yf = {}
        for _, row in h.iterrows():
            sym = row.get("tradingsymbol", "")
            ikey = row.get("instrument_token") or row.get("instrument_key") or ""
            yf_t = f"{sym}.NS"
            tickers.append((yf_t, ikey))
            value_by_yf[yf_t] = float(row.get("current_value", 0))
        return tickers, value_by_yf

    def _run_opt(self, kind: str, kwargs: dict):
        tickers, value_by_yf = self._build_tickers()
        if not tickers:
            return {"error": "No holdings to optimize."}
        rets = self.opt.returns(tickers, lookback_days=kwargs.get("lookback_days", 365))
        if rets.empty or rets.shape[1] < 2:
            return {"error": "Insufficient overlapping price history for optimization."}
        max_w = kwargs.get("max_weight", 0.25)
        if kind == "max_sharpe":
            res = self.opt.max_sharpe(rets, max_weight=max_w)
        else:
            res = self.opt.target_return(rets, target=kwargs["target_return"], max_weight=max_w)
        rebal = self.opt.rebalance_suggestion(value_by_yf, res)
        return {
            "weights_pct": (res.weights * 100).round(2).to_dict(),
            "expected_return_pct": round(res.expected_return * 100, 2),
            "volatility_pct": round(res.volatility * 100, 2),
            "sharpe": round(res.sharpe, 3),
            "rebalance": rebal.reset_index().rename(columns={"index": "ticker"}).to_dict("records"),
        }

    def _dispatch(self, name, kwargs):
        snap = self._snapshot()
        if name == "get_portfolio_snapshot":
            return {
                "summary": snap.summary,
                "holdings": snap.holdings.to_dict("records") if not snap.holdings.empty else [],
                "positions": snap.positions.to_dict("records") if not snap.positions.empty else [],
                "allocation": snap.allocation.to_dict("records") if not snap.allocation.empty else [],
            }
        if name == "concentration_risk":
            return self.pm.concentration_risk(snap, kwargs.get("threshold_pct", 15)).to_dict("records")
        if name == "underperformers":
            return self.pm.underperformers(snap, kwargs.get("pnl_threshold", -10)).to_dict("records")
        if name == "optimize_max_sharpe":
            return self._run_opt("max_sharpe", kwargs)
        if name == "optimize_target_return":
            return self._run_opt("target_return", kwargs)
        if name == "generate_report":
            return ReportBuilder().build(snap)
        raise ValueError(f"unknown tool {name}")

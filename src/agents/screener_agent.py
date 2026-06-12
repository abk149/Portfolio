from __future__ import annotations

from src.agents.base import BaseAgent
from src.screener import ScreenerEngine


class ScreenerAgent(BaseAgent):
    SYSTEM = (
        "You are a stock screener for Indian equities. Use the scan tool to gather "
        "technical + fundamental scores across a universe, then explain which names "
        "look attractive and why, citing RSI, trend, P/E, ROE, growth. Always state "
        "trade idea constraints (timeframe, entry zone, invalidation). Never recommend "
        "a name without a tool call backing it. Indian context: prices in INR."
    )

    TOOLS = [
        {
            "name": "scan_universe",
            "description": "Run combined technical + fundamental scan and return ranked rows.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "universe": {"type": "string", "default": "nifty50"},
                    "top_n": {"type": "integer", "default": 10},
                },
            },
        },
        {
            "name": "details",
            "description": "Detailed technical + fundamental snapshot for a single symbol from a prior scan.",
            "input_schema": {
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
        },
    ]

    def __init__(self):
        super().__init__()
        self.eng = ScreenerEngine()
        self._last_scan = None

    def _dispatch(self, name, kwargs):
        if name == "scan_universe":
            df = self.eng.scan(kwargs.get("universe", "nifty50"))
            self._last_scan = df
            cols = ["name", "symbol", "ltp", "combined", "tech_score", "fund_score",
                    "recommendation", "rsi", "ret_3m_pct", "PE", "ROE", "sector"]
            return df[[c for c in cols if c in df.columns]].head(kwargs.get("top_n", 10)).to_dict("records")
        if name == "details":
            if self._last_scan is None:
                self._last_scan = self.eng.scan("nifty50")
            row = self._last_scan[self._last_scan["symbol"] == kwargs["symbol"]]
            return row.to_dict("records")[0] if not row.empty else {"error": "not found"}
        raise ValueError(name)

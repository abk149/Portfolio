from __future__ import annotations

from src.agents.base import BaseAgent
from src.intraday import IntradayAnalyzer, IntradayScanner


class IntradayAgent(BaseAgent):
    SYSTEM = (
        "You are an intraday coach. You have two superpowers: (1) review the user's "
        "past intraday trades and surface honest, specific weaknesses, and (2) scan "
        "the live market for high-probability intraday setups with strict R-multiples. "
        "Always specify: direction, entry, stop, T1 (1R), T2 (2R), and the signal that "
        "triggered it. Never invent trades. If the scanner returns nothing, say so."
    )

    TOOLS = [
        {
            "name": "analyze_my_trades",
            "description": "Analyze the user's past intraday trades from Upstox.",
            "input_schema": {
                "type": "object",
                "properties": {"days": {"type": "integer", "default": 90}},
            },
        },
        {
            "name": "scan_opportunities",
            "description": "Live intraday opportunity scan across a universe.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "universe": {"type": "string", "default": "nifty50"},
                    "min_score": {"type": "integer", "default": 40},
                },
            },
        },
    ]

    def __init__(self):
        super().__init__()
        self.ana = IntradayAnalyzer()
        self.scan_eng = IntradayScanner()

    def _dispatch(self, name, kwargs):
        if name == "analyze_my_trades":
            return self.ana.analyze(kwargs.get("days", 90))
        if name == "scan_opportunities":
            df = self.scan_eng.scan(kwargs.get("universe", "nifty50"), kwargs.get("min_score", 40))
            return df.to_dict("records") if not df.empty else []
        raise ValueError(name)

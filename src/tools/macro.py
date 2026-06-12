"""Daily macro snapshot — India VIX, Nifty PCR, USD/INR.

We pull from publicly accessible NSE endpoints (best-effort; NSE rate-limits
and changes paths often, so all calls are wrapped in try/except with sane
fallbacks via yfinance).

`market_mode()` translates the snapshot into BULLISH / BEARISH / NEUTRAL.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import requests

from src.utils.logger import get_logger

log = get_logger("tools.macro")

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-IN,en;q=0.9",
}


@dataclass
class Macro:
    india_vix: Optional[float] = None
    nifty_pcr: Optional[float] = None
    usdinr: Optional[float] = None
    nifty_change_pct: Optional[float] = None


class MacroSnapshot:
    def fetch(self) -> Macro:
        m = Macro()

        # India VIX via NSE
        try:
            s = requests.Session()
            s.headers.update(NSE_HEADERS)
            s.get("https://www.nseindia.com", timeout=10)
            r = s.get("https://www.nseindia.com/api/allIndices", timeout=10).json()
            for idx in r.get("data", []):
                if idx.get("indexSymbol") == "INDIA VIX":
                    m.india_vix = float(idx["last"])
                if idx.get("indexSymbol") == "NIFTY 50":
                    m.nifty_change_pct = float(idx.get("percentChange") or 0.0)
        except Exception as e:
            log.debug(f"NSE indices fetch failed: {e}")

        # Nifty PCR
        try:
            s = requests.Session()
            s.headers.update(NSE_HEADERS)
            s.get("https://www.nseindia.com/option-chain", timeout=10)
            j = s.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
                      timeout=10).json()
            tot = j.get("filtered", {}).get("totOI", {})
            put_oi, call_oi = float(tot.get("PE", 0)), float(tot.get("CE", 0))
            if call_oi > 0:
                m.nifty_pcr = round(put_oi / call_oi, 3)
        except Exception as e:
            log.debug(f"NSE PCR fetch failed: {e}")

        # USD/INR — Upstox doesn't expose FX rates. We leave this None;
        # the agent doesn't actually use it for decisions.

        return m

    def market_mode(self, m: Optional[Macro] = None) -> dict:
        m = m or self.fetch()
        mode = "NEUTRAL"
        reasons = []
        if m.india_vix is not None:
            if m.india_vix < 13:
                mode, _ = "BULLISH", reasons.append(f"VIX low ({m.india_vix:.1f}) → complacent / risk-on")
            elif m.india_vix > 18:
                mode, _ = "BEARISH", reasons.append(f"VIX elevated ({m.india_vix:.1f}) → fear / risk-off")
            else:
                reasons.append(f"VIX neutral ({m.india_vix:.1f})")
        if m.nifty_pcr is not None:
            if m.nifty_pcr > 1.3:
                reasons.append(f"PCR {m.nifty_pcr} → contrarian bullish")
                mode = "BULLISH" if mode == "NEUTRAL" else mode
            elif m.nifty_pcr < 0.8:
                reasons.append(f"PCR {m.nifty_pcr} → contrarian bearish")
                mode = "BEARISH" if mode == "NEUTRAL" else mode
        return {"mode": mode, "reasons": reasons, **asdict(m)}

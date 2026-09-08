"""Daily macro snapshot — India VIX, Nifty PCR, USD/INR.

We pull from publicly accessible NSE endpoints (best-effort; NSE rate-limits
and changes paths often, so all calls are wrapped in try/except with sane
fallbacks via yfinance).

`market_mode()` translates the snapshot into BULLISH / BEARISH / NEUTRAL.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
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


def _yahoo_ltp(ticker: str) -> Optional[float]:
    """Free public quote. Works on-device and needs no key or cookie."""
    try:
        from src.data import yahoo
        return yahoo.ltp(ticker)
    except Exception as e:
        log.debug(f"yahoo {ticker} failed: {e}")
        return None


class MacroSnapshot:

    def _nse_session(self) -> requests.Session:
        """NSE hands out cookies on the HTML pages and rejects API calls made
        without them (and without a matching Referer). Warm the session first."""
        s = requests.Session()
        s.headers.update(NSE_HEADERS)
        try:
            s.get("https://www.nseindia.com", timeout=10)
        except Exception as e:
            log.debug(f"NSE cookie warmup failed: {e}")
        return s

    def fetch(self) -> tuple[Macro, list[str]]:
        """Snapshot + a list of human-readable notes about anything missing.

        Every field has a free public fallback, because NSE's APIs are bot-
        protected and fail most of the time from a phone. Returning a blank
        tile with no explanation (which is what this used to do) is worse than
        useless — the user can't tell a calm market from a broken feed.
        """
        m = Macro()
        notes: list[str] = []

        # ── India VIX + Nifty move: NSE first, Yahoo as the fallback ──
        try:
            s = self._nse_session()
            r = s.get("https://www.nseindia.com/api/allIndices", timeout=10).json()
            for idx in r.get("data", []):
                if idx.get("indexSymbol") == "INDIA VIX":
                    m.india_vix = float(idx["last"])
                if idx.get("indexSymbol") == "NIFTY 50":
                    m.nifty_change_pct = float(idx.get("percentChange") or 0.0)
        except Exception as e:
            log.debug(f"NSE indices fetch failed: {e}")

        if m.india_vix is None:
            m.india_vix = _yahoo_ltp("^INDIAVIX")
        if m.nifty_change_pct is None:
            try:
                from src.data import yahoo
                q = yahoo.quote("^NSEI")
                last, prev = q.get("last_price"), q.get("previous_close")
                if last and prev:
                    m.nifty_change_pct = round((last - prev) / prev * 100, 2)
            except Exception as e:
                log.debug(f"yahoo nifty change failed: {e}")

        # ── USD/INR ──
        # This used to be hard-wired to None with a comment saying the agent
        # didn't need it — but it is displayed on the DR-Quant macro tiles, so
        # it just read blank forever. Yahoo serves the pair for free.
        m.usdinr = _yahoo_ltp("USDINR=X")
        if m.usdinr is None:
            notes.append("USD/INR unavailable — the FX quote source didn't respond.")

        # ── Nifty put/call ratio ──
        # Only NSE publishes the option chain, and it is aggressively bot-
        # protected; expect this one to fail from a mobile IP.
        try:
            s = self._nse_session()
            s.headers.update({"Referer": "https://www.nseindia.com/option-chain"})
            s.get("https://www.nseindia.com/option-chain", timeout=10)
            j = s.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
                      timeout=12).json()
            tot = (j.get("filtered") or {}).get("totOI") or {}
            put_oi, call_oi = float(tot.get("PE") or 0), float(tot.get("CE") or 0)
            if call_oi > 0:
                m.nifty_pcr = round(put_oi / call_oi, 3)
        except Exception as e:
            log.debug(f"NSE PCR fetch failed: {e}")
        if m.nifty_pcr is None:
            notes.append("Nifty PCR unavailable — NSE's option-chain API blocks "
                         "non-browser requests, which is normal from a phone. "
                         "The regime call falls back to VIX.")

        return m, notes

    def market_mode(self, m: Optional[Macro] = None) -> dict:
        notes: list[str] = []
        if m is None:
            m, notes = self.fetch()

        mode = "NEUTRAL"
        reasons: list[str] = []

        if m.india_vix is not None:
            if m.india_vix < 13:
                mode = "BULLISH"
                reasons.append(f"VIX low ({m.india_vix:.1f}) → complacent / risk-on")
            elif m.india_vix > 18:
                mode = "BEARISH"
                reasons.append(f"VIX elevated ({m.india_vix:.1f}) → fear / risk-off")
            else:
                reasons.append(f"VIX neutral ({m.india_vix:.1f})")
        else:
            reasons.append("VIX unavailable — regime is a best guess.")

        if m.nifty_pcr is not None:
            if m.nifty_pcr > 1.3:
                reasons.append(f"PCR {m.nifty_pcr} → contrarian bullish")
                mode = "BULLISH" if mode == "NEUTRAL" else mode
            elif m.nifty_pcr < 0.8:
                reasons.append(f"PCR {m.nifty_pcr} → contrarian bearish")
                mode = "BEARISH" if mode == "NEUTRAL" else mode
            else:
                reasons.append(f"PCR {m.nifty_pcr} → balanced positioning")

        if m.nifty_change_pct is not None:
            reasons.append(f"Nifty {m.nifty_change_pct:+.2f}% today")
        if m.usdinr is not None:
            reasons.append(f"USD/INR {m.usdinr:.2f}")

        return {
            "mode": mode,
            # `regime` is the same value under the name the UI reads. The
            # mismatch between this dict's "mode" and the app's "regime" lookup
            # is why the tile was blank.
            "regime": mode,
            "reasons": reasons,
            "notes": notes,
            "as_of": datetime.now().isoformat(timespec="minutes"),
            **asdict(m),
        }

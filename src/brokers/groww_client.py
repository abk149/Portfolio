"""Groww broker client — mirrors UpstoxClient's method surface and output
shapes so every downstream consumer works unchanged.

⚠️  ENDPOINT PATHS: implemented against the documented Groww Trading API v1
(https://groww.in/trade-api/docs). Groww occasionally revises paths/field
names; every endpoint + response field is isolated in the clearly-marked
constants/normalisers below so a mismatch is a one-line fix, not a rewrite.

Robustness: every call is wrapped — a failure returns an empty list / dict /
DataFrame (never raises mid-scan), exactly like the Upstox client's _get does
on non-200. Only missing AUTH raises BrokerAuthError (same contract as Upstox).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

import pandas as pd
import requests

from config import settings
from src.brokers.base import BrokerAuthError
from src.brokers.groww_auth import load_token
from src.utils.logger import get_logger

log = get_logger("brokers.groww")

BASE = "https://api.groww.in"
API_VERSION = "1.0"

# ── endpoint paths (adjust here if Groww docs differ) ──
EP_HOLDINGS   = "/v1/holdings/user"
EP_POSITIONS  = "/v1/positions/user"
EP_MARGINS    = "/v1/margins/detail/user"
EP_LTP        = "/v1/live-data/ltp"
EP_QUOTE      = "/v1/live-data/quote"
EP_HISTORICAL = "/v1/historical/candle/range"
EP_ORDER      = "/v1/order/create"

# Groww interval (minutes) for our generic interval names
_INTERVAL_MIN = {"day": 1440, "week": 1440 * 7, "30minute": 30, "1minute": 1}


def _sym_from_key(instrument_key: Optional[str], default: str = "") -> str:
    """Our pipeline passes Upstox-style keys ('NSE_EQ|INE...') or yf tickers
    ('RELIANCE.NS'). Groww wants the bare trading symbol ('RELIANCE')."""
    if not instrument_key:
        return default
    s = str(instrument_key)
    if "|" in s:                      # 'NSE_EQ|INE002A01018' → can't recover symbol
        return default                # caller should pass a real symbol for Groww
    return s.replace(".NS", "").replace(".BO", "").upper()


class GrowwClient:
    """Lightweight authenticated Groww client."""

    def __init__(self, access_token: Optional[str] = None):
        self._token = access_token or load_token()
        if not self._token:
            raise BrokerAuthError(
                "No Groww access token. Paste a daily token in the app, set "
                "GROWW_ACCESS_TOKEN, or configure GROWW_API_KEY/SECRET (TOTP)."
            )

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "X-API-VERSION": API_VERSION,
        }

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        try:
            r = requests.get(f"{BASE}{path}", headers=self._headers,
                             params=params, timeout=30)
        except Exception as e:
            log.debug(f"groww GET {path} error: {e}")
            return None
        if r.status_code in (401, 403):
            raise BrokerAuthError(f"Groww token expired/invalid ({r.status_code}).")
        if r.status_code != 200:
            log.debug(f"groww {path} → {r.status_code}: {r.text[:200]}")
            return None
        try:
            j = r.json()
        except Exception:
            return None
        # Groww wraps payloads under "data" (or "payload"); unwrap if present
        if isinstance(j, dict):
            return j.get("data", j.get("payload", j))
        return j

    # ---------------- account ----------------
    def profile(self) -> dict:
        # Groww has no dedicated profile endpoint; a successful margins call
        # proves the token works. Return a minimal profile so broker_status()
        # and the dashboard show "authenticated".
        m = self._get(EP_MARGINS)
        if m is None:
            # Force the auth contract: if even margins fails non-auth, surface it
            raise BrokerAuthError("Groww: could not verify token (margins call failed).")
        return {"user_name": "Groww account", "broker": "groww", "raw_margins": m}

    def funds(self) -> dict:
        return self._get(EP_MARGINS) or {}

    # ---------------- portfolio (normalised to Upstox holding shape) ----------
    def holdings(self) -> list[dict]:
        raw = self._get(EP_HOLDINGS) or {}
        rows = raw.get("holdings", raw) if isinstance(raw, dict) else raw
        rows = rows or []

        # Groww's holdings payload carries NO price — only isin/symbol/qty/avg.
        # Enrich with live LTP so current_value/pnl/weights are real (and don't
        # divide-by-zero into NaN downstream). One batched LTP call.
        symbols = [(h.get("trading_symbol") or h.get("tradingsymbol")
                    or h.get("symbol") or "") for h in rows]
        price_map: dict = {}
        try:
            price_map = self.ltp([s for s in symbols if s])
        except BrokerAuthError:
            raise
        except Exception as e:
            log.debug(f"groww holdings LTP enrich failed: {e}")

        out = []
        for h in rows:
            qty = float(h.get("quantity") or h.get("qty") or 0)
            avg = float(h.get("average_price") or h.get("avg_price") or 0)
            sym = (h.get("trading_symbol") or h.get("tradingsymbol")
                   or h.get("symbol") or "")
            ltp = float(h.get("ltp") or h.get("last_price") or h.get("current_price")
                        or price_map.get(sym, {}).get("last_price") or avg)
            out.append({
                "tradingsymbol": sym,
                "quantity": qty,
                "average_price": avg,
                "last_price": ltp,
                "close_price": float(h.get("close_price") or h.get("prev_close") or ltp),
                "pnl": round((ltp - avg) * qty, 2),
                "day_change": float(h.get("day_change") or 0),
                "day_change_percentage": float(h.get("day_change_perc") or 0),
                "instrument_token": h.get("isin") or h.get("instrument_token") or "",
                "isin": h.get("isin", ""),
                "exchange": h.get("exchange", "NSE"),
            })
        return out

    def positions(self) -> list[dict]:
        raw = self._get(EP_POSITIONS) or {}
        rows = raw.get("positions", raw) if isinstance(raw, dict) else raw
        out = []
        for p in (rows or []):
            out.append({
                "tradingsymbol": (p.get("trading_symbol") or p.get("symbol") or ""),
                "quantity": float(p.get("quantity") or p.get("net_quantity") or 0),
                "average_price": float(p.get("average_price") or 0),
                "last_price": float(p.get("ltp") or p.get("last_price") or 0),
                "pnl": float(p.get("pnl") or p.get("net_pnl") or 0),
                "unrealised": float(p.get("unrealised_pnl") or p.get("unrealized") or 0),
                "realised": float(p.get("realised_pnl") or p.get("realized") or 0),
                "exchange": p.get("exchange", "NSE"),
            })
        return out

    def order_book(self) -> list[dict]:
        return self._get("/v1/order/list") or []

    def trades_today(self) -> list[dict]:
        return self._get("/v1/trade/list") or []

    def trade_history(self, start: date, end: date, segment: str = "EQ") -> list[dict]:
        # Groww exposes order/trade history; shape varies. Best-effort.
        return self._get("/v1/trade/list", params={
            "from": start.isoformat(), "to": end.isoformat()}) or []

    # ---------------- market data ----------------
    def _exchange_symbol(self, symbol: str, exchange: str = "NSE") -> str:
        return f"{exchange}_{symbol.upper()}"

    def ltp(self, instruments: list[str]) -> dict:
        """instruments: bare symbols or yf tickers. Returns {sym: {last_price}}.

        Groww LTP: GET /v1/live-data/ltp?segment=CASH&exchange_symbols=NSE_X,NSE_Y
        → payload {"NSE_X": 2334.2, ...}. Up to 50 symbols per call.
        """
        syms = [_sym_from_key(i, i) for i in instruments if _sym_from_key(i, i)]
        out: dict = {}
        for i in range(0, len(syms), 50):
            chunk = syms[i:i + 50]
            ex = ",".join(self._exchange_symbol(s) for s in chunk)
            data = self._get(EP_LTP, params={"segment": "CASH", "exchange_symbols": ex})
            if isinstance(data, dict):
                for ex_sym, price in data.items():
                    sym = str(ex_sym).split("_", 1)[-1]      # 'NSE_RELIANCE' → 'RELIANCE'
                    if isinstance(price, (int, float)):
                        out[sym] = {"last_price": float(price)}
        return out

    def quote(self, instruments: list[str]) -> dict:
        """Groww quote: GET /v1/live-data/quote?exchange=NSE&segment=CASH&trading_symbol=X
        → payload {last_price, ohlc{...}, volume, ...}."""
        out = {}
        for ins in instruments:
            sym = _sym_from_key(ins, ins)
            data = self._get(EP_QUOTE, params={
                "exchange": "NSE", "segment": "CASH", "trading_symbol": sym})
            if data:
                out[sym] = data
        return out

    def candles(self, instrument_key: str, interval: str = "day",
                to_date: Optional[date] = None,
                from_date: Optional[date] = None) -> pd.DataFrame:
        sym = _sym_from_key(instrument_key)
        if not sym:
            # Groww needs a real symbol; an Upstox 'NSE_EQ|INE...' key can't be
            # used here. Return empty so callers fall back / skip gracefully.
            return pd.DataFrame()
        to_date = to_date or date.today()
        from_date = from_date or (to_date - timedelta(days=365))
        # Groww wants 'yyyy-MM-dd HH:mm:ss' (space, not ISO 'T') or epoch seconds.
        data = self._get(EP_HISTORICAL, params={
            "trading_symbol": sym, "exchange": "NSE", "segment": "CASH",
            "interval_in_minutes": _INTERVAL_MIN.get(interval, 1440),
            "start_time": f"{from_date.isoformat()} 00:00:00",
            "end_time": f"{to_date.isoformat()} 23:59:59",
        })
        candles = (data or {}).get("candles", []) if isinstance(data, dict) else (data or [])
        if not candles:
            return pd.DataFrame()
        # Groww candle rows: [epoch/ts, open, high, low, close, volume]
        cols = ["ts", "open", "high", "low", "close", "volume"]
        df = pd.DataFrame(candles).iloc[:, :6]
        df.columns = cols[:df.shape[1]]
        df["oi"] = 0
        try:
            df["ts"] = pd.to_datetime(df["ts"], unit="s", errors="coerce").fillna(
                pd.to_datetime(df["ts"], errors="coerce"))
        except Exception:
            df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
        df = df.dropna(subset=["ts"]).sort_values("ts").set_index("ts")
        return df

    def intraday_candles(self, instrument_key: str,
                         interval: str = "30minute") -> pd.DataFrame:
        return self.candles(instrument_key, interval=interval,
                            from_date=date.today(), to_date=date.today())

    # ---------------- orders ----------------
    def place_order(self, body: dict) -> dict:
        try:
            r = requests.post(f"{BASE}{EP_ORDER}", headers=self._headers,
                             json=body, timeout=30)
            return r.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}

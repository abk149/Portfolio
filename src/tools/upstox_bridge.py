"""Order-placement bridge to your existing Upstox Agent / direct Upstox API.

By default this runs in DRY_RUN mode — it logs the intent and returns a
synthetic ack. Flip `live=True` (or set UPSTOX_LIVE_ORDERS=1) to actually
place orders. Even in live mode we refuse anything without a stop-loss.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Optional

import requests

from config import settings
from src.upstox.client import UpstoxClient
from src.utils.logger import get_logger

log = get_logger("tools.upstox_bridge")


@dataclass
class OrderIntent:
    symbol: str
    instrument_key: str
    side: str            # BUY | SELL
    qty: int
    order_type: str      # MARKET | LIMIT
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    target: Optional[float] = None
    product: str = "I"   # I=Intraday, D=Delivery
    tag: str = "D-R1-Quant"


class UpstoxBridge:
    def __init__(self, live: Optional[bool] = None):
        self.up = UpstoxClient() if live or os.getenv("UPSTOX_LIVE_ORDERS") == "1" else None
        self.live = bool(self.up)

    # ---- read ----
    def positions(self) -> list[dict]:
        return UpstoxClient().positions()

    def holdings(self) -> list[dict]:
        return UpstoxClient().holdings()

    # ---- write ----
    def place(self, intent: OrderIntent) -> dict:
        if intent.side not in ("BUY", "SELL"):
            return {"ok": False, "error": "side must be BUY/SELL"}
        if intent.stop_loss is None:
            return {"ok": False, "error": "refuse to place order without stop_loss"}

        if not self.live:
            log.info(f"[DRY] {intent}")
            return {"ok": True, "dry_run": True, "intent": intent.__dict__,
                    "ts": int(time.time())}

        body = {
            "quantity": intent.qty,
            "product": intent.product,
            "validity": "DAY",
            "price": intent.price or 0,
            "tag": intent.tag,
            "instrument_token": intent.instrument_key,
            "order_type": intent.order_type,
            "transaction_type": intent.side,
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False,
        }
        r = requests.post(
            f"{settings.upstox_base_url}/order/place",
            json=body,
            headers={"Authorization": f"Bearer {self.up._token}",
                     "Accept": "application/json", "Api-Version": "2.0"},
            timeout=30,
        )
        log.info(f"[LIVE] {intent.side} {intent.symbol} qty={intent.qty} → {r.status_code}")
        return r.json()

"""Portfolio performance analytics.

Provides XIRR calculation, historical equity-curve reconstruction,
period returns, winners/losers ranking, and opportunity-miss detection.

All heavy computation is done here. The dashboard calls
``PerformanceAnalyzer.full_report()`` as a background job.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional

import pandas as pd
from scipy.optimize import brentq

from src.upstox.client import UpstoxClient
from src.data.cache import get_or_set
from src.utils.logger import get_logger

log = get_logger("portfolio.analytics")


# ---------------------------------------------------------------------------
# XIRR solver
# ---------------------------------------------------------------------------

def xirr(cashflows: list[tuple[date, float]], guess: float = 0.1) -> Optional[float]:
    """Compute the annualised internal rate of return (XIRR).

    Parameters
    ----------
    cashflows : list of (date, amount)
        Negative = outflow (purchase), positive = inflow (sale / current value).
    guess : float
        Starting guess for the root finder.

    Returns
    -------
    float or None
        Annualised rate (0.18 = 18 %).  ``None`` if the solver fails.
    """
    if len(cashflows) < 2:
        return None

    dates, amounts = zip(*cashflows)
    d0 = min(dates)

    def _npv(rate: float) -> float:
        return sum(
            amt / (1.0 + rate) ** ((dt - d0).days / 365.25)
            for dt, amt in zip(dates, amounts)
        )

    try:
        return brentq(_npv, -0.999, 100.0, maxiter=500)
    except (ValueError, RuntimeError):
        # brentq needs a sign change in the interval; if not, try Newton-ish
        try:
            from scipy.optimize import newton
            return newton(_npv, guess, maxiter=500)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# PerformanceAnalyzer
# ---------------------------------------------------------------------------

class PerformanceAnalyzer:
    """Orchestrates all portfolio-performance computations."""

    # Indian financial year starts 1 April.
    _FY_START_MONTH = 4

    def __init__(self, upstox: Optional[UpstoxClient] = None):
        self.upstox = upstox or UpstoxClient()

    # ------------------------------------------------------------------
    # Trade history (multi-FY fetch with pagination)
    # ------------------------------------------------------------------

    @staticmethod
    def _fy_ranges(earliest_year: int = 2018) -> list[tuple[date, date]]:
        """Return (start, end) for each FY from *earliest_year* to today."""
        today = date.today()
        current_fy_start_year = (
            today.year if today.month >= 4 else today.year - 1
        )
        ranges = []
        for y in range(current_fy_start_year, earliest_year - 1, -1):
            start = date(y, 4, 1)
            end = min(date(y + 1, 3, 31), today)
            if start > today:
                continue
            ranges.append((start, end))
        return ranges

    def fetch_all_trades(self) -> pd.DataFrame:
        """Fetch trade history across all financial years.

        Returns a DataFrame with columns:
            date, symbol, instrument_key, side, quantity, price, amount, trade_id
        Cached for 1 hour.
        """
        def _fetch():
            all_trades: list[dict] = []
            for start, end in self._fy_ranges():
                page = 1
                while True:
                    raw = self.upstox._get(
                        "/charges/historical-trades",
                        params={
                            "segment": "EQ",
                            "start_date": start.isoformat(),
                            "end_date": end.isoformat(),
                            "page_number": page,
                            "page_size": 100,
                        },
                    )
                    if not raw:
                        break

                    # Upstox wraps the list in a 'trades' key sometimes
                    trades_list = raw if isinstance(raw, list) else (
                        raw.get("trades") or raw.get("data") or []
                    )
                    if not trades_list:
                        break

                    for t in trades_list:
                        all_trades.append({
                            "date": pd.to_datetime(
                                t.get("trade_date") or t.get("order_timestamp", "")
                            ).date() if (t.get("trade_date") or t.get("order_timestamp")) else None,
                            "symbol": (
                                t.get("tradingsymbol") or t.get("trading_symbol") or
                                t.get("scrip_name") or ""
                            ),
                            "instrument_key": t.get("instrument_token") or t.get("instrument_key", ""),
                            "side": (t.get("transaction_type") or t.get("trade_type") or "").upper(),
                            "quantity": int(t.get("quantity") or t.get("trade_qty") or 0),
                            "price": float(t.get("price") or t.get("trade_price") or 0),
                            "amount": float(t.get("trade_value") or t.get("amount") or 0),
                            "trade_id": t.get("trade_id") or t.get("order_id", ""),
                        })

                    if len(trades_list) < 100:
                        break  # last page
                    page += 1

                # Stop scanning older FYs if we already got nothing for this one
                if not all_trades:
                    continue

            if not all_trades:
                return pd.DataFrame()

            df = pd.DataFrame(all_trades)
            df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

            # Ensure amount is filled
            if "amount" in df.columns:
                mask = df["amount"] == 0
                df.loc[mask, "amount"] = df.loc[mask, "quantity"] * df.loc[mask, "price"]

            return df

        return get_or_set("performance", "all_trades", 3600, _fetch)

    # ------------------------------------------------------------------
    # Equity curve reconstruction
    # ------------------------------------------------------------------

    def build_equity_curve(
        self,
        trades: pd.DataFrame,
        holdings: list[dict],
    ) -> pd.DataFrame:
        """Build a daily portfolio-value time series.

        Strategy: replay trades chronologically to track positions held on
        each date, then sample weekly (every 5 trading days) to keep API
        calls manageable — one ``candles()`` call per unique instrument.

        Returns DataFrame with columns: ``date``, ``portfolio_value``,
        ``invested_capital``.
        """
        if trades.empty and not holdings:
            return pd.DataFrame()

        # ---- Step 1: collect all instruments we've ever traded ----
        instruments: dict[str, str] = {}  # symbol → instrument_key
        for _, t in trades.iterrows():
            sym = t.get("symbol", "")
            ikey = t.get("instrument_key", "")
            if sym and ikey:
                instruments[sym] = ikey
        # Also include current holdings
        for h in holdings:
            sym = h.get("tradingsymbol", "")
            ikey = h.get("instrument_key") or h.get("instrument_token", "")
            if sym and ikey:
                instruments[sym] = ikey

        if not instruments:
            return pd.DataFrame()

        # ---- Step 2: fetch daily close prices for all instruments ----
        first_trade_date = trades["date"].min() if not trades.empty else date.today() - timedelta(days=365)
        lookback = (date.today() - first_trade_date).days + 30

        price_cache: dict[str, pd.Series] = {}  # symbol → Series[date→close]
        for sym, ikey in instruments.items():
            try:
                df = self.upstox.candles(
                    ikey, interval="day",
                    to_date=date.today(),
                    from_date=first_trade_date - timedelta(days=5),
                )
                if not df.empty:
                    closes = df["close"].copy()
                    closes.index = closes.index.date  # type: ignore
                    price_cache[sym] = closes
            except Exception as e:
                log.debug(f"candles failed for {sym}: {e}")

        if not price_cache:
            return pd.DataFrame()

        # ---- Step 3: replay trades to build position ledger ----
        # positions_on_date[d] = {sym: qty_held}
        # We only track at daily granularity.
        positions: dict[str, int] = {}  # running position
        cost_basis: dict[str, float] = {}  # running invested capital per sym
        total_invested = 0.0
        total_withdrawn = 0.0

        trade_events: list[tuple[date, str, int, float]] = []  # (date, sym, signed_qty, amount)
        for _, t in trades.iterrows():
            d = t["date"]
            sym = t["symbol"]
            qty = int(t["quantity"])
            price = float(t["price"])
            side = t["side"]
            if side == "BUY":
                trade_events.append((d, sym, qty, qty * price))
            elif side == "SELL":
                trade_events.append((d, sym, -qty, -(qty * price)))

        trade_events.sort(key=lambda x: x[0])

        # Build date range
        all_dates = sorted(set(
            d for s in price_cache.values() for d in s.index
        ))
        if not all_dates:
            return pd.DataFrame()

        # Sample at weekly intervals for performance
        sampled_dates = all_dates[::5]
        if all_dates[-1] not in sampled_dates:
            sampled_dates.append(all_dates[-1])

        event_idx = 0
        curve_rows = []

        for d in sampled_dates:
            # Apply all trades up to this date
            while event_idx < len(trade_events) and trade_events[event_idx][0] <= d:
                _, sym, signed_qty, amount = trade_events[event_idx]
                positions[sym] = positions.get(sym, 0) + signed_qty
                if signed_qty > 0:
                    cost_basis[sym] = cost_basis.get(sym, 0) + amount
                    total_invested += amount
                else:
                    # Sell: reduce cost basis proportionally
                    held = positions[sym] + abs(signed_qty)  # qty before sell
                    if held > 0:
                        frac = abs(signed_qty) / held
                        removed = cost_basis.get(sym, 0) * frac
                        cost_basis[sym] = cost_basis.get(sym, 0) - removed
                        total_withdrawn += abs(amount)
                    else:
                        total_withdrawn += abs(amount)
                event_idx += 1

            # Compute portfolio value
            port_value = 0.0
            for sym, qty in positions.items():
                if qty <= 0:
                    continue
                prices = price_cache.get(sym)
                if prices is None or prices.empty:
                    continue
                # Get closest price on or before date d
                valid = prices[prices.index <= d]
                if valid.empty:
                    continue
                port_value += qty * float(valid.iloc[-1])

            invested_net = total_invested - total_withdrawn
            curve_rows.append({
                "date": d.isoformat() if isinstance(d, date) else str(d),
                "portfolio_value": round(port_value, 2),
                "invested_capital": round(max(invested_net, 0), 2),
            })

        return pd.DataFrame(curve_rows)

    # ------------------------------------------------------------------
    # Returns computation
    # ------------------------------------------------------------------

    @staticmethod
    def compute_returns(equity_curve: pd.DataFrame) -> dict:
        """Compute 1Y, 3Y, 5Y, and since-inception returns from the equity curve."""
        if equity_curve.empty or len(equity_curve) < 2:
            return {}

        ec = equity_curve.copy()
        ec["date"] = pd.to_datetime(ec["date"])
        ec = ec.sort_values("date")

        latest_value = ec["portfolio_value"].iloc[-1]
        latest_date = ec["date"].iloc[-1]

        results = {}
        for label, years in [("1Y", 1), ("3Y", 3), ("5Y", 5)]:
            target_date = latest_date - pd.DateOffset(years=years)
            past = ec[ec["date"] <= target_date]
            if past.empty:
                continue
            past_value = past["portfolio_value"].iloc[-1]
            past_invested = past["invested_capital"].iloc[-1]
            if past_value > 0:
                absolute = (latest_value - past_value) / past_value
                cagr = (latest_value / past_value) ** (1.0 / years) - 1.0
                results[label] = {
                    "absolute_pct": round(absolute * 100, 2),
                    "cagr_pct": round(cagr * 100, 2),
                }

        # Since inception
        first_value = ec["portfolio_value"].iloc[0]
        if first_value > 0:
            inception_days = (latest_date - ec["date"].iloc[0]).days
            inception_years = max(inception_days / 365.25, 0.01)
            absolute = (latest_value - first_value) / first_value
            cagr = (latest_value / first_value) ** (1.0 / inception_years) - 1.0
            results["inception"] = {
                "absolute_pct": round(absolute * 100, 2),
                "cagr_pct": round(cagr * 100, 2),
                "years": round(inception_years, 1),
            }

        return results

    # ------------------------------------------------------------------
    # XIRR from trades
    # ------------------------------------------------------------------

    def compute_xirr(self, trades: pd.DataFrame, current_value: float) -> Optional[float]:
        """Compute XIRR using actual trade cash flows + current portfolio value."""
        if trades.empty:
            return None

        cashflows: list[tuple[date, float]] = []
        for _, t in trades.iterrows():
            d = t["date"]
            amt = float(t["amount"])
            side = t["side"]
            if side == "BUY":
                cashflows.append((d, -amt))    # outflow
            elif side == "SELL":
                cashflows.append((d, amt))     # inflow

        if not cashflows:
            return None

        # Terminal cash flow: current portfolio value
        cashflows.append((date.today(), current_value))

        rate = xirr(cashflows)
        return round(rate * 100, 2) if rate is not None else None

    # ------------------------------------------------------------------
    # Winners / Losers
    # ------------------------------------------------------------------

    @staticmethod
    def winners_losers(holdings: list[dict]) -> dict:
        """Rank current holdings by P&L percentage."""
        rows = []
        for h in holdings:
            qty = float(h.get("quantity", 0))
            avg = float(h.get("average_price", 0))
            ltp = float(h.get("last_price", 0))
            if qty <= 0 or avg <= 0:
                continue
            invested = qty * avg
            current = qty * ltp
            pnl = current - invested
            pnl_pct = (pnl / invested) * 100
            rows.append({
                "symbol": h.get("tradingsymbol", "?"),
                "quantity": int(qty),
                "avg_price": round(avg, 2),
                "ltp": round(ltp, 2),
                "invested": round(invested, 2),
                "current_value": round(current, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
            })

        rows.sort(key=lambda r: r["pnl_pct"], reverse=True)
        winners = [r for r in rows if r["pnl_pct"] > 0]
        losers = [r for r in reversed(rows) if r["pnl_pct"] <= 0]

        return {
            "winners": winners[:15],
            "losers": losers[:15],
        }

    # ------------------------------------------------------------------
    # Opportunity misses
    # ------------------------------------------------------------------

    def opportunity_misses(
        self, trades: pd.DataFrame, holdings: list[dict], threshold_pct: float = 20.0,
    ) -> list[dict]:
        """Find stocks that were completely sold but current price is much higher.

        An "opportunity miss" is a stock where:
        1. Net position is zero (fully exited).
        2. Current price is > threshold_pct% above the average sell price.
        """
        if trades.empty:
            return []

        # Build per-symbol ledger
        sym_ledger: dict[str, dict] = {}  # sym → {total_buy_qty, total_sell_qty, sell_prices, ikey}
        for _, t in trades.iterrows():
            sym = t["symbol"]
            if not sym:
                continue
            if sym not in sym_ledger:
                sym_ledger[sym] = {
                    "total_buy_qty": 0, "total_sell_qty": 0,
                    "sell_amounts": [], "sell_dates": [],
                    "ikey": t.get("instrument_key", ""),
                }
            if t["side"] == "BUY":
                sym_ledger[sym]["total_buy_qty"] += t["quantity"]
            elif t["side"] == "SELL":
                sym_ledger[sym]["total_sell_qty"] += t["quantity"]
                sym_ledger[sym]["sell_amounts"].append(
                    (t["quantity"], t["price"], t["date"])
                )

        # Current holdings symbols (skip these)
        held_symbols = {h.get("tradingsymbol", "") for h in holdings}

        # Find fully-exited symbols
        exited = {}
        for sym, ledger in sym_ledger.items():
            if sym in held_symbols:
                continue
            net = ledger["total_buy_qty"] - ledger["total_sell_qty"]
            if net <= 0 and ledger["sell_amounts"]:
                # Compute weighted average sell price
                total_qty = sum(q for q, _, _ in ledger["sell_amounts"])
                if total_qty > 0:
                    avg_sell = sum(q * p for q, p, _ in ledger["sell_amounts"]) / total_qty
                    last_sell_date = max(d for _, _, d in ledger["sell_amounts"])
                    exited[sym] = {
                        "avg_sell_price": avg_sell,
                        "last_sell_date": last_sell_date,
                        "ikey": ledger["ikey"],
                        "total_qty_sold": total_qty,
                    }

        if not exited:
            return []

        # Fetch current prices
        misses = []
        ikeys = [v["ikey"] for v in exited.values() if v["ikey"]]
        if not ikeys:
            return []

        # Batch LTP — Upstox supports comma-separated instrument keys
        batch_size = 20
        ltp_map: dict[str, float] = {}
        for i in range(0, len(ikeys), batch_size):
            batch = ikeys[i:i + batch_size]
            try:
                result = self.upstox.ltp(batch)
                if result:
                    for key, val in result.items():
                        if isinstance(val, dict):
                            ltp_map[key] = float(val.get("last_price", 0))
                        elif isinstance(val, (int, float)):
                            ltp_map[key] = float(val)
            except Exception as e:
                log.debug(f"LTP fetch failed for batch: {e}")

        for sym, info in exited.items():
            ikey = info["ikey"]
            current_price = ltp_map.get(ikey, 0)
            if current_price <= 0:
                # Try matching by partial key
                for k, v in ltp_map.items():
                    if ikey in k or k in ikey:
                        current_price = v
                        break

            if current_price <= 0:
                continue

            avg_sell = info["avg_sell_price"]
            if avg_sell <= 0:
                continue

            missed_gain_pct = ((current_price - avg_sell) / avg_sell) * 100
            if missed_gain_pct >= threshold_pct:
                misses.append({
                    "symbol": sym,
                    "last_sell_date": (
                        info["last_sell_date"].isoformat()
                        if isinstance(info["last_sell_date"], date)
                        else str(info["last_sell_date"])
                    ),
                    "avg_sell_price": round(avg_sell, 2),
                    "current_price": round(current_price, 2),
                    "missed_gain_pct": round(missed_gain_pct, 2),
                    "qty_sold": int(info["total_qty_sold"]),
                    "missed_value": round(
                        (current_price - avg_sell) * info["total_qty_sold"], 2
                    ),
                })

        misses.sort(key=lambda m: m["missed_gain_pct"], reverse=True)
        return misses[:20]

    # ------------------------------------------------------------------
    # Full report
    # ------------------------------------------------------------------

    def full_report(self) -> dict:
        """Orchestrate all analytics into a single JSON-serializable result.

        This is the method called by the dashboard background job.
        """
        log.info("Starting performance analysis …")

        # Current holdings
        holdings_raw = self.upstox.holdings() or []
        current_value = sum(
            float(h.get("quantity", 0)) * float(h.get("last_price", 0))
            for h in holdings_raw
        )
        invested_total = sum(
            float(h.get("quantity", 0)) * float(h.get("average_price", 0))
            for h in holdings_raw
        )

        # Trade history
        log.info("Fetching trade history …")
        trades = self.fetch_all_trades()
        log.info(f"Found {len(trades)} trades")

        # Equity curve
        log.info("Building equity curve …")
        equity_curve = self.build_equity_curve(trades, holdings_raw)
        log.info(f"Equity curve: {len(equity_curve)} data points")

        # Returns
        returns = self.compute_returns(equity_curve)

        # XIRR
        log.info("Computing XIRR …")
        xirr_val = self.compute_xirr(trades, current_value)

        # Winners / Losers
        wl = self.winners_losers(holdings_raw)

        # Opportunity misses
        log.info("Scanning for opportunity misses …")
        misses = self.opportunity_misses(trades, holdings_raw)

        first_trade_date = (
            trades["date"].min().isoformat()
            if not trades.empty and trades["date"].min() is not None
            else None
        )

        result = {
            "equity_curve": equity_curve.to_dict("records") if not equity_curve.empty else [],
            "xirr": xirr_val,
            "returns": returns,
            "winners": wl["winners"],
            "losers": wl["losers"],
            "opportunity_misses": misses,
            "summary": {
                "total_invested": round(invested_total, 2),
                "current_value": round(current_value, 2),
                "total_pnl": round(current_value - invested_total, 2),
                "total_pnl_pct": round(
                    ((current_value - invested_total) / invested_total * 100)
                    if invested_total > 0 else 0, 2
                ),
                "first_trade_date": first_trade_date,
                "total_trades": len(trades),
                "n_holdings": len(holdings_raw),
            },
        }

        log.info("Performance analysis complete.")
        return result

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
from src.utils.compat import brentq, newton

from src.upstox.client import UpstoxClient
from src.brokers import get_broker
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
    except (ValueError, RuntimeError, Exception):
        # brentq needs a sign change in the interval; if not, try Newton-ish
        try:
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
        self.upstox = upstox or get_broker()

    # ------------------------------------------------------------------
    # Free public-source fallbacks (used when the broker can't serve prices,
    # e.g. Groww live-data/historical not entitled). Keeps Performance working
    # without Upstox.
    # ------------------------------------------------------------------
    @staticmethod
    def _yahoo_candles(symbol: str, lookback_days: int):
        try:
            from src.data import yahoo
            return yahoo.daily(symbol, lookback_days)
        except Exception as e:
            log.debug(f"yahoo candles fallback failed for {symbol}: {e}")
            import pandas as _pd
            return _pd.DataFrame()

    @staticmethod
    def _yahoo_ltp(symbol: str):
        try:
            from src.data import yahoo
            return yahoo.ltp(symbol)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Trade history (multi-FY fetch with pagination)
    # ------------------------------------------------------------------

    @staticmethod
    def _fy_ranges(years_back: int = 3) -> list[tuple[date, date]]:
        """(start, end) for each of the last `years_back` financial years.

        Upstox's /charges/historical-trades only serves the LAST 3 FINANCIAL
        YEARS — requesting older ranges returns 405/400. Cap to that window and
        never send a future end-date (the current FY ends at `today`).
        """
        today = date.today()
        current_fy_start_year = today.year if today.month >= 4 else today.year - 1
        earliest_year = current_fy_start_year - (max(1, years_back) - 1)
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
                api_df = pd.DataFrame()
            else:
                api_df = pd.DataFrame(all_trades)
                if "trade_id" in api_df.columns:
                    api_df = api_df.drop_duplicates(subset=["trade_id"], keep="first")
                api_df = api_df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

            # Load uploaded trades if present
            from pathlib import Path
            uploaded_df = pd.DataFrame()
            cache_dir = Path(".cache/user_trades")
            if cache_dir.exists():
                files = list(cache_dir.glob("*"))
                if files:
                    latest_file = max(files, key=lambda f: f.stat().st_mtime)
                    try:
                        uploaded_df = self._load_user_uploaded_trades(str(latest_file))
                    except Exception as e:
                        log.error(f"Failed parsing uploaded trades: {e}")

            if api_df.empty and uploaded_df.empty:
                return pd.DataFrame()

            # Merge
            df = pd.concat([uploaded_df, api_df], ignore_index=True)
            # Deduplicate across both sets.
            # We match on date, symbol, side, quantity, price(rounded)
            # This prevents overlap if the uploaded file includes recent trades that the API also fetched.
            if not df.empty:
                df["_p_round"] = df["price"].round(1)
                df = df.drop_duplicates(subset=["date", "symbol", "side", "quantity", "_p_round"], keep="last")
                df = df.drop(columns=["_p_round"])
                df = df.sort_values("date").reset_index(drop=True)


            # Ensure amount is filled
            if "amount" in df.columns:
                mask = df["amount"] == 0
                df.loc[mask, "amount"] = df.loc[mask, "quantity"] * df.loc[mask, "price"]

            return df

        return get_or_set("performance", "all_trades", 3600, _fetch)

    def _load_user_uploaded_trades(self, filepath: str) -> pd.DataFrame:
        """Parse a user-uploaded Excel or CSV file of Upstox trades."""
        import pandas as pd
        import uuid
        
        if filepath.endswith(".csv"):
            df = pd.read_csv(filepath)
        else:
            df = pd.read_excel(filepath)

        if df.empty:
            return pd.DataFrame()

        # fuzzy matching for columns
        cols = {c.lower().strip(): c for c in df.columns if isinstance(c, str)}
        
        date_col = next((cols[c] for c in ["trade date", "date", "order timestamp"] if c in cols), None)
        sym_col = next((cols[c] for c in ["scrip name", "tradingsymbol", "trading_symbol", "symbol", "instrument"] if c in cols), None)
        side_col = next((cols[c] for c in ["transaction type", "trade type", "buy/sell", "side", "type"] if c in cols), None)
        qty_col = next((cols[c] for c in ["quantity", "trade qty", "qty"] if c in cols), None)
        price_col = next((cols[c] for c in ["price", "trade price", "rate", "avg price"] if c in cols), None)
        amount_col = next((cols[c] for c in ["trade value", "amount", "net amount", "total", "net value"] if c in cols), None)

        if not all([date_col, sym_col, side_col, qty_col, price_col]):
            missing = [k for k, v in {"date": date_col, "symbol": sym_col, "side": side_col, "quantity": qty_col, "price": price_col}.items() if v is None]
            raise ValueError(f"Missing required columns. Found: {list(df.columns)}. Missing logical equivalents for: {missing}")

        parsed = []
        for _, row in df.iterrows():
            d_val = row[date_col]
            if pd.isna(d_val): continue
            
            try:
                dt = pd.to_datetime(d_val).date()
            except:
                continue
                
            qty = 0
            try: qty = int(row[qty_col])
            except: pass
            
            price = 0.0
            try: price = float(row[price_col])
            except: pass
            
            amt = 0.0
            if amount_col and not pd.isna(row[amount_col]):
                try: amt = float(row[amount_col])
                except: amt = float(qty) * price
            else:
                amt = float(qty) * price
                
            # Parse side (could be 'B' or 'BUY' or 'Sell')
            side_str = str(row[side_col]).strip().upper()
            if side_str.startswith('B'): side_str = "BUY"
            elif side_str.startswith('S'): side_str = "SELL"
            
            parsed.append({
                "date": dt,
                "symbol": str(row[sym_col]).strip() if not pd.isna(row[sym_col]) else "",
                "instrument_key": "", # missing in most exports, will resolve via matching later
                "side": side_str,
                "quantity": qty,
                "price": price,
                "amount": amt,
                "trade_id": "ul_" + str(uuid.uuid4().hex[:8])
            })
            
        res_df = pd.DataFrame(parsed)
        
        # Resolve instrument keys using KB
        if not res_df.empty:
            try:
                from src.kb import KnowledgeBase
                kb = KnowledgeBase.get()
                sym_to_ikey = {s.get("symbol", "").upper(): s.get("instrument_key", "") for s in kb.all_stocks()}
                
                def _map_ikey(row):
                    sym = row["symbol"].upper()
                    # clean up names if needed, e.g. "ZOMATO"
                    if sym in sym_to_ikey: return sym_to_ikey[sym]
                    return ""
                
                res_df["instrument_key"] = res_df.apply(_map_ikey, axis=1)
            except:
                pass
                
        return res_df

    @staticmethod
    def _filter_intraday_trades(trades_df: pd.DataFrame) -> pd.DataFrame:
        """Cancel out matching BUY and SELL quantities on the same day for the same symbol
        to completely remove intraday trading volume from the equity curve.
        """
        if trades_df.empty:
            return trades_df

        kept_trades = []
        # Process day by day, instrument_key by instrument_key
        for (date_val, ikey), group in trades_df.groupby(["date", "instrument_key"]):
            buys = group[group["side"] == "BUY"].to_dict("records")
            sells = group[group["side"] == "SELL"].to_dict("records")

            # Match and reduce quantities
            while buys and sells:
                b = buys[0]
                s = sells[0]
                match_qty = min(b["quantity"], s["quantity"])
                b["quantity"] -= match_qty
                s["quantity"] -= match_qty

                if b["quantity"] == 0:
                    buys.pop(0)
                if s["quantity"] == 0:
                    sells.pop(0)

            # Add remaining non-zero trades
            kept_trades.extend([t for t in buys if t["quantity"] > 0])
            kept_trades.extend([t for t in sells if t["quantity"] > 0])

        if not kept_trades:
            return pd.DataFrame(columns=trades_df.columns)

        return pd.DataFrame(kept_trades).sort_values("date").reset_index(drop=True)

    @staticmethod
    def _filter_short_term_trades(trades_df: pd.DataFrame, min_days: int = 2) -> pd.DataFrame:
        """Cancel out BUY and SELL matching quantities where the holding period
        is <= min_days. This removes BTST and short-term trades.
        """
        if trades_df.empty:
            return trades_df

        trades_df = trades_df.copy()
        trades_df['date_dt'] = pd.to_datetime(trades_df['date'])
        qty_to_keep = {t['trade_id']: t['quantity'] for _, t in trades_df.iterrows()}
        
        for ikey, group in trades_df.sort_values('date_dt').groupby('instrument_key'):
            buy_q = []
            for _, t in group.iterrows():
                tid = t['trade_id']
                if t['side'] == 'BUY':
                    buy_q.append([tid, t['date_dt'], t['quantity']])
                elif t['side'] == 'SELL':
                    sell_rem = t['quantity']
                    while sell_rem > 0 and buy_q:
                        b = buy_q[0]
                        match = min(sell_rem, b[2])
                        hold_days = (t['date_dt'] - b[1]).days
                        
                        if hold_days <= min_days:
                            qty_to_keep[b[0]] -= match
                            qty_to_keep[tid] -= match
                            
                        b[2] -= match
                        sell_rem -= match
                        if b[2] == 0:
                            buy_q.pop(0)
                            
        kept = []
        for _, t in trades_df.iterrows():
            keep_q = qty_to_keep[t['trade_id']]
            if keep_q > 0:
                new_t = t.to_dict()
                new_t['quantity'] = keep_q
                if new_t.get('amount') and t['quantity'] > 0:
                    new_t['amount'] = new_t['amount'] * (keep_q / t['quantity'])
                kept.append(new_t)
                
        res = pd.DataFrame(kept)
        if 'date_dt' in res.columns:
            res = res.drop(columns=['date_dt'])
        return res

    def _inject_initial_positions(self, trades_df: pd.DataFrame, holdings: list[dict]) -> pd.DataFrame:
        """Inject synthetic trades at the start of the fetched history
        to account for stocks bought before the API limit (3 financial years).
        Uses historical candle data to accurately estimate cost basis for exited stocks.
        Also completely removes corrupted MTF trades where the Upstox API lost the SELL legs.
        """
        if trades_df.empty and not holdings:
            return trades_df

        h_map = {h.get("instrument_token"): h for h in holdings if h.get("instrument_token")}
        
        # Calculate final qty from trades
        calc_final = {}
        for _, t in trades_df.iterrows():
            ikey = t.get("instrument_key")
            if not ikey: continue
            q = int(t.get("quantity", 0))
            if t.get("side") == "BUY":
                calc_final[ikey] = calc_final.get(ikey, 0) + q
            elif t.get("side") == "SELL":
                calc_final[ikey] = calc_final.get(ikey, 0) - q

        corrupted_ikeys = set()
        excess_buys = {}
        
        # First pass: Find corrupted positions
        for ikey in set(calc_final.keys()) | set(h_map.keys()):
            actual = int(h_map[ikey]["quantity"]) if ikey in h_map else 0
            calc = calc_final.get(ikey, 0)
            missing = actual - calc
            
            if missing < 0:
                if actual == 0:
                    # Fully exited, but trades show we hold it -> API missing sells entirely!
                    corrupted_ikeys.add(ikey)
                else:
                    # Partially missing sells
                    excess_buys[ikey] = abs(missing)
                    
        # Filter out entirely corrupted stocks (e.g. Zomato, GAIL where MTF sells are missing)
        if corrupted_ikeys:
            trades_df = trades_df[~trades_df["instrument_key"].isin(corrupted_ikeys)]
            
        # Drop excess buys for partially corrupted stocks
        if excess_buys:
            kept_trades = []
            for _, t in trades_df.iloc[::-1].iterrows():
                ikey = t.get("instrument_key")
                q = int(t.get("quantity", 0))
                if t.get("side") == "BUY" and excess_buys.get(ikey, 0) > 0:
                    drop_q = min(q, excess_buys[ikey])
                    excess_buys[ikey] -= drop_q
                    q -= drop_q
                if q > 0:
                    new_t = t.to_dict()
                    if new_t.get("amount") and t.get("quantity", 0) > 0:
                        new_t["amount"] = float(new_t["amount"]) * (q / int(t["quantity"]))
                    new_t["quantity"] = q
                    kept_trades.append(new_t)
            kept_trades.reverse()
            trades_df = pd.DataFrame(kept_trades)

        if trades_df.empty:
            first_date = date.today()
        else:
            first_date = trades_df["date"].min()
            
        synth_date = first_date - timedelta(days=1)
        hist_start = synth_date - timedelta(days=365 * 3)
        
        synthetic_trades = []
        all_ikeys = set(calc_final.keys()) | set(h_map.keys())
        
        for ikey in all_ikeys:
            if ikey in corrupted_ikeys:
                continue
                
            actual_final = int(h_map[ikey]["quantity"]) if ikey in h_map else 0
            calc = calc_final.get(ikey, 0)
            
            # Since we dropped excess buys, calc_final needs recalculating logically,
            # but missing_qty > 0 is unaffected by the drops.
            missing_qty = actual_final - calc
            
            if missing_qty > 0:
                sym = trades_df[trades_df["instrument_key"] == ikey].iloc[0]["symbol"] if not trades_df[trades_df["instrument_key"] == ikey].empty else h_map[ikey].get("tradingsymbol", "")
                avg_price = float(h_map[ikey]["average_price"]) if ikey in h_map else 0.0
                
                # If fully exited, fetch a realistic historical price to avoid inflating invested capital
                if avg_price == 0.0:
                    sym_trades = trades_df[trades_df["instrument_key"] == ikey]
                    if not sym_trades.empty:
                        try:
                            df_hist = self.upstox.candles(ikey, interval="month", from_date=hist_start, to_date=synth_date)
                            if not df_hist.empty:
                                avg_price = float(df_hist["close"].mean())
                            else:
                                sells = sym_trades[sym_trades["side"] == "SELL"]
                                avg_price = float(sells.iloc[0]["price"]) if not sells.empty else float(sym_trades.iloc[0]["price"])
                        except Exception:
                            sells = sym_trades[sym_trades["side"] == "SELL"]
                            avg_price = float(sells.iloc[0]["price"]) if not sells.empty else float(sym_trades.iloc[0]["price"])

                synthetic_trades.append({
                    "date": synth_date,
                    "symbol": sym,
                    "instrument_key": ikey,
                    "side": "BUY",
                    "quantity": missing_qty,
                    "price": avg_price,
                    "amount": missing_qty * avg_price,
                    "trade_id": f"synthetic_init_{ikey}"
                })

        if not synthetic_trades:
            return trades_df

        synth_df = pd.DataFrame(synthetic_trades)
        for col in trades_df.columns:
            if col not in synth_df.columns:
                synth_df[col] = None
                
        combined = pd.concat([synth_df, trades_df], ignore_index=True)
        return combined.sort_values("date").reset_index(drop=True)

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
            df = pd.DataFrame()
            try:
                df = self.upstox.candles(
                    ikey, interval="day",
                    to_date=date.today(),
                    from_date=first_trade_date - timedelta(days=5),
                )
            except Exception as e:
                log.debug(f"broker candles failed for {sym}: {e}")
            # Failproof: broker gave nothing (e.g. Groww historical not entitled)
            # → free public source so the equity curve still builds.
            if df is None or df.empty:
                df = self._yahoo_candles(sym, lookback)
            if df is not None and not df.empty:
                closes = df["close"].copy()
                closes.index = closes.index.date  # type: ignore
                price_cache[sym] = closes

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

            invested_net = sum(cost_basis.values())
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
                # Failproof: broker LTP unavailable → free public source.
                current_price = self._yahoo_ltp(sym) or 0

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

    @staticmethod
    def all_stocks_summary(trades: pd.DataFrame, holdings: list[dict]) -> list[dict]:
        """Generate a complete summary of all traded stocks including total bought,
        total sold, current holding, and net realized P&L.
        """
        if trades.empty:
            return []

        h_map = {h.get("instrument_token"): h for h in holdings if h.get("instrument_token")}
        summary = {}

        for _, t in trades.iterrows():
            ikey = t.get("instrument_key")
            if not ikey: continue
            
            sym = t.get("symbol")
            if ikey not in summary:
                summary[ikey] = {
                    "symbol": h_map[ikey].get("tradingsymbol", sym) if ikey in h_map else sym,
                    "total_bought_qty": 0,
                    "total_sold_qty": 0,
                    "realized_pnl": 0.0,
                    "current_qty": int(h_map.get(ikey, {}).get("quantity", 0)),
                    "current_value": 0.0
                }
                
                if ikey in h_map:
                    summary[ikey]["current_value"] = summary[ikey]["current_qty"] * float(h_map[ikey].get("last_price", 0))
            
            q = int(t.get("quantity", 0))
            amt = float(t.get("amount", 0))
            
            if t.get("side") == "BUY":
                summary[ikey]["total_bought_qty"] += q
                summary[ikey]["realized_pnl"] -= amt
            elif t.get("side") == "SELL":
                summary[ikey]["total_sold_qty"] += q
                summary[ikey]["realized_pnl"] += amt

        for ikey, data in summary.items():
            if data["current_qty"] > 0 and ikey in h_map:
                avg_price = float(h_map[ikey].get("average_price", 0))
                last_price = float(h_map[ikey].get("last_price", 0))
                unrealized_pnl = data["current_qty"] * (last_price - avg_price)
                data["unrealized_pnl"] = unrealized_pnl
                data["total_pnl"] = data["realized_pnl"] + unrealized_pnl
            else:
                data["unrealized_pnl"] = 0.0
                data["total_pnl"] = data["realized_pnl"]

        result = list(summary.values())
        result.sort(key=lambda x: x["total_pnl"], reverse=True)
        return result

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
        raw_trades = self.fetch_all_trades()
        
        # Filter intraday trades FIRST
        filtered_trades = self._filter_intraday_trades(raw_trades)
        
        # Filter BTST / Short term trades (<= 2 days)
        filtered_trades = self._filter_short_term_trades(filtered_trades, min_days=2)
        
        # Inject synthetic initial positions for historical holdings
        trades = self._inject_initial_positions(filtered_trades, holdings_raw)
        
        log.info(f"Raw trades: {len(raw_trades)} -> Filtered intraday: {len(filtered_trades)} -> Total with synthetic: {len(trades)}")

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
            "all_stocks": self.all_stocks_summary(trades, holdings_raw),
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

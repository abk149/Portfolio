"""Combined technical + fundamental screener.

Two-stage funnel for big universes:
  Stage 1 — technical scan on EVERY instrument (cheap, daily candles only).
            Keeps only names above `tech_min`.
  Stage 2 — fundamental scan on the technical survivors (expensive, yfinance .info).
            Combined score = 0.6*tech + 0.4*fund.

Run:
    ScreenerEngine().scan('all_nse', tech_min=60)
    ScreenerEngine().scan('nifty50')                     # legacy small-universe path
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import pandas as pd
from rich.progress import Progress, TextColumn, BarColumn, TimeRemainingColumn

from src.data import MarketData
from src.data.instruments import resolve_universe
from src.screener.fundamental import fundamental_score
from src.screener.technical import technical_score
from src.upstox.client import UpstoxClient
from src.utils.logger import get_logger

log = get_logger("screener")


def _recommendation(tech: float | None, fund: float | None) -> str:
    if tech is None:
        return "INSUFFICIENT_DATA"
    if fund is None:
        if tech >= 70:
            return "TECH_BUY"
        if tech >= 55:
            return "TECH_WATCH"
        return "AVOID"
    combined = 0.6 * tech + 0.4 * fund
    if combined >= 70:
        return "STRONG_BUY"
    if combined >= 55:
        return "BUY"
    if combined >= 40:
        return "HOLD"
    if combined >= 25:
        return "AVOID"
    return "SELL"


class ScreenerEngine:
    # Upstox quota-limits hard on a 3000-stock bulk scan. 2 workers + the
    # adaptive rate limiter in upstox/client.py keeps us under the threshold;
    # anything that still 429s is skipped and retried on the next
    # (incremental) run rather than blocking.
    def __init__(self, upstox: Optional[UpstoxClient] = None, workers: int = 2):
        try:
            self.upstox = upstox or UpstoxClient()
        except Exception:
            log.warning("No Upstox session — screener using yfinance only.")
            self.upstox = None
        self.md = MarketData(self.upstox)
        self.workers = workers

    # ---------- stage 1: technical only ----------
    def _technical_row(self, name, yf_t, nse_t, ikey) -> Optional[dict]:
        try:
            df = self.md.daily(yf_t, ikey, lookback_days=400)
            if df is None or df.empty or len(df) < 60:
                return None
            tech = technical_score(df)
            if tech.get("score") is None:
                return None
            return {
                "name": str(name), "symbol": nse_t,
                "instrument_key": ikey, "yf_ticker": yf_t,
                "ltp": float(df["close"].iloc[-1]),
                "tech_score": tech["score"],
                "rsi": tech.get("rsi"),
                "ret_1m_pct": tech.get("ret_1m_pct"),
                "ret_3m_pct": tech.get("ret_3m_pct"),
                "near_52w_high_pct": tech.get("near_52w_high_pct"),
                "atr_pct": tech.get("atr_pct"),
            }
        except Exception as e:
            log.debug(f"tech scan failed {name}: {e}")
            return None

    def technical_scan(self, universe: str, tech_min: float = 60.0) -> pd.DataFrame:
        items = resolve_universe(universe)
        log.info(f"Technical scan: {len(items)} instruments in '{universe}'")
        rows: list[dict] = []
        with Progress(TextColumn("[bold blue]tech"), BarColumn(),
                      TextColumn("{task.completed}/{task.total}"),
                      TimeRemainingColumn()) as prog:
            t = prog.add_task("scan", total=len(items))
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                futs = [ex.submit(self._technical_row, *it) for it in items]
                for f in as_completed(futs):
                    r = f.result()
                    if r and r["tech_score"] >= tech_min:
                        rows.append(r)
                    prog.advance(t)
        df = pd.DataFrame(rows).sort_values("tech_score", ascending=False) if rows else pd.DataFrame()
        log.info(f"Technical survivors (≥ {tech_min}): {len(df)}")
        return df

    # ---------- stage 2: fundamentals on survivors ----------
    def _fundamental_row(self, row: dict) -> dict:
        """Fundamentals via screener.in → Yahoo fallback. Adapts to the
        fundamental_score() schema."""
        sym = row["symbol"]
        log.info(f"  ◯ {sym} — screener.in …")
        try:
            from src.tools.screener_in import fetch_fundamentals
            raw = fetch_fundamentals(sym) or {}
            # If screener.in produced nothing usable, explicitly hit Yahoo
            # here (instead of relying on the silent fallback inside the
            # screener_in helper). Visible in this engine logger.
            has_ratios = any(raw.get(k) is not None
                             for k in ("pe", "roe_pct", "debt_to_equity",
                                       "market_cap_cr"))
            if not has_ratios:
                log.info(f"  ⤳ {sym} — screener.in empty, trying Yahoo …")
                from src.tools.yahoo_fundamentals import fetch_fundamentals_yahoo
                yahoo = fetch_fundamentals_yahoo(sym) or {}
                if any(yahoo.get(k) is not None
                       for k in ("pe", "roe_pct", "debt_to_equity",
                                 "market_cap_cr")):
                    raw = {**raw, **{k: v for k, v in yahoo.items()
                                     if v is not None}}
                    log.info(f"  ⤳ {sym} — Yahoo OK "
                             f"(PE={raw.get('pe')} ROE={raw.get('roe_pct')})")
                else:
                    log.info(f"  ⤳ {sym} — Yahoo also empty")
            adapted = {
                "trailingPE": raw.get("pe"),
                "priceToBook": None,        # screener.in doesn't expose directly
                "returnOnEquity": (raw.get("roe_pct") / 100) if raw.get("roe_pct") is not None else None,
                "debtToEquity": raw.get("debt_to_equity"),
                "earningsGrowth": (raw.get("profit_growth_pct") / 100) if raw.get("profit_growth_pct") is not None else None,
                "revenueGrowth": (raw.get("sales_growth_pct") / 100) if raw.get("sales_growth_pct") is not None else None,
                "profitMargins": None,
                "freeCashflow": None,
                "marketCap": (raw.get("market_cap_cr") or 0) * 1e7 if raw.get("market_cap_cr") else None,
                "sector": raw.get("sector"),
                "industry": None,
            }
            f = fundamental_score(adapted)
            row.update({
                "fund_score": f.get("score"),
                "PE": adapted["trailingPE"],
                "ROE": adapted["returnOnEquity"],
                "DE": adapted["debtToEquity"],
                "earningsGrowth": adapted["earningsGrowth"],
                "revenueGrowth": adapted["revenueGrowth"],
                "sector": adapted["sector"],
                "marketCap": adapted["marketCap"],
            })
            score = f.get("score")
            if score is None or score == 0:
                log.info(f"  • {sym} no fundamentals on screener.in (empty page or parse miss)")
            else:
                roe_str = f"{adapted['returnOnEquity']*100:.1f}%" if adapted["returnOnEquity"] is not None else "—"
                log.info(
                    f"  ✓ {sym} fund={score:.0f} "
                    f"PE={adapted['trailingPE']} "
                    f"ROE={roe_str} "
                    f"D/E={adapted['debtToEquity']}"
                )
        except Exception as e:
            log.info(f"  ✗ {sym} fund scrape failed: {type(e).__name__}: {e}")
            row["fund_score"] = None
        return row

    def fundamental_scan(self, tech_df: pd.DataFrame, fund_min: float = 50.0) -> pd.DataFrame:
        if tech_df.empty:
            return tech_df
        log.info(f"Fundamental scan on {len(tech_df)} technical survivors")
        rows: list[dict] = []
        with Progress(TextColumn("[bold green]fund"), BarColumn(),
                      TextColumn("{task.completed}/{task.total}"),
                      TimeRemainingColumn()) as prog:
            t = prog.add_task("scan", total=len(tech_df))
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                futs = [ex.submit(self._fundamental_row, r) for r in tech_df.to_dict("records")]
                for f in as_completed(futs):
                    rows.append(f.result())
                    prog.advance(t)

        df = pd.DataFrame(rows)
        df["combined"] = df.apply(
            lambda r: round(0.6 * r["tech_score"] + 0.4 * r["fund_score"], 1)
            if pd.notna(r.get("fund_score")) else None, axis=1,
        )
        df["recommendation"] = df.apply(
            lambda r: _recommendation(r.get("tech_score"), r.get("fund_score")), axis=1
        )
        df = df.sort_values("combined", ascending=False, na_position="last")
        return df[df["fund_score"].fillna(-1) >= fund_min] if fund_min else df

    # ---------- public funnel ----------
    def scan(
        self,
        universe: str = "nifty50",
        tech_min: float = 60.0,
        fund_min: float = 50.0,
    ) -> pd.DataFrame:
        tech = self.technical_scan(universe, tech_min)
        if tech.empty:
            return tech
        return self.fundamental_scan(tech, fund_min)

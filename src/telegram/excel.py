"""Build domain Excel reports for delivery over Telegram.

Each helper takes domain objects and writes a multi-sheet xlsx, returning the path.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from config import settings


def _out(name: str) -> Path:
    d = settings.cache_dir / "reports"
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    return d / f"{name}_{ts}.xlsx"


def build_screener_xlsx(scan_df: pd.DataFrame, tech_df: pd.DataFrame | None = None) -> Path:
    path = _out("screener")
    with pd.ExcelWriter(path, engine="xlsxwriter") as xw:
        if not scan_df.empty:
            scan_df.to_excel(xw, sheet_name="Recommendations", index=False)
            top = scan_df[scan_df["recommendation"].isin(["STRONG_BUY", "BUY"])] \
                if "recommendation" in scan_df.columns else scan_df.head(20)
            top.to_excel(xw, sheet_name="Top Buys", index=False)
        if tech_df is not None and not tech_df.empty:
            tech_df.to_excel(xw, sheet_name="Technical Survivors", index=False)
    return path


def build_intraday_xlsx(analysis: dict | None = None, scan_df: pd.DataFrame | None = None) -> Path:
    path = _out("intraday")
    with pd.ExcelWriter(path, engine="xlsxwriter") as xw:
        if analysis:
            top = {k: v for k, v in analysis.items() if not isinstance(v, list)}
            pd.DataFrame(list(top.items()), columns=["metric", "value"]).to_excel(
                xw, sheet_name="Summary", index=False
            )
            for key in ("best", "worst", "by_symbol", "by_dow", "mistakes"):
                v = analysis.get(key)
                if not v:
                    continue
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    pd.DataFrame(v).to_excel(xw, sheet_name=key[:31], index=False)
                else:
                    pd.DataFrame({"item": v}).to_excel(xw, sheet_name=key[:31], index=False)
        if scan_df is not None and not scan_df.empty:
            scan_df.to_excel(xw, sheet_name="Live Opportunities", index=False)
    return path


def build_optimizer_xlsx(opt_result, rebalance_df: pd.DataFrame | None,
                         frontier_df: pd.DataFrame | None = None) -> Path:
    path = _out("optimizer")
    with pd.ExcelWriter(path, engine="xlsxwriter") as xw:
        pd.DataFrame([
            ("expected_return_pct", round(opt_result.expected_return * 100, 2)),
            ("volatility_pct", round(opt_result.volatility * 100, 2)),
            ("sharpe", round(opt_result.sharpe, 3)),
            ("risk_free_pct", round(opt_result.risk_free * 100, 2)),
        ], columns=["metric", "value"]).to_excel(xw, sheet_name="Summary", index=False)

        (opt_result.weights * 100).round(2).rename("weight_pct").reset_index() \
            .rename(columns={"index": "ticker"}) \
            .to_excel(xw, sheet_name="Target Weights", index=False)

        if rebalance_df is not None and not rebalance_df.empty:
            rebalance_df.reset_index().rename(columns={"index": "ticker"}) \
                .to_excel(xw, sheet_name="Rebalance Plan", index=False)
        if frontier_df is not None and not frontier_df.empty:
            frontier_df.to_excel(xw, sheet_name="Efficient Frontier", index=False)
    return path

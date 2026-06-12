"""Generate CSV + HTML + XLSX reports from a PortfolioSnapshot."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
from jinja2 import Template

from config import settings
from src.portfolio.manager import PortfolioSnapshot

HTML_TPL = Template(
    """
<!doctype html><html><head><meta charset="utf-8">
<title>Portfolio Report — {{ ts }}</title>
<style>
 body{font-family:-apple-system,Segoe UI,sans-serif;margin:24px;color:#222}
 h1,h2{margin-top:1.4em}
 table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}
 th,td{border:1px solid #ddd;padding:6px 8px;text-align:right}
 th{background:#f4f4f4;text-align:left}
 td:first-child,th:first-child{text-align:left}
 .pos{color:#0a7d2c}.neg{color:#b00020}
 .kpi{display:inline-block;margin:6px 18px 6px 0;padding:10px 14px;border:1px solid #ddd;border-radius:6px;background:#fafafa}
 .kpi b{font-size:18px}
</style></head><body>
<h1>Portfolio Report</h1><p>Generated {{ ts }}</p>
<div>
 <div class="kpi">Invested<br><b>₹{{ s.holdings_invested|round(0)|int }}</b></div>
 <div class="kpi">Current<br><b>₹{{ s.holdings_value|round(0)|int }}</b></div>
 <div class="kpi">Unrealised P&L<br>
  <b class="{{ 'pos' if s.holdings_pnl>=0 else 'neg' }}">
   ₹{{ s.holdings_pnl|round(0)|int }} ({{ s.holdings_pnl_pct|round(2) }}%)</b></div>
 <div class="kpi">Day change<br>
  <b class="{{ 'pos' if s.day_change_value>=0 else 'neg' }}">₹{{ s.day_change_value|round(0)|int }}</b></div>
 <div class="kpi">Positions P&L<br>
  <b class="{{ 'pos' if s.positions_pnl>=0 else 'neg' }}">₹{{ s.positions_pnl|round(0)|int }}</b></div>
</div>
<h2>Holdings</h2>{{ holdings_html|safe }}
<h2>Allocation</h2>{{ allocation_html|safe }}
{% if positions_html %}<h2>Positions</h2>{{ positions_html|safe }}{% endif %}
</body></html>
"""
)


class ReportBuilder:
    def __init__(self, out_dir: Path | None = None):
        self.out = out_dir or (settings.cache_dir / "reports")
        self.out.mkdir(parents=True, exist_ok=True)

    def build(self, snap: PortfolioSnapshot, name: str = "portfolio") -> dict:
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        base = self.out / f"{name}_{ts}"

        csv_path = base.with_suffix(".csv")
        if not snap.holdings.empty:
            snap.holdings.to_csv(csv_path, index=False)

        html_path = base.with_suffix(".html")
        html_path.write_text(
            HTML_TPL.render(
                ts=ts,
                s=snap.summary,
                holdings_html=snap.holdings.to_html(index=False, float_format="%.2f") if not snap.holdings.empty else "<i>No holdings</i>",
                allocation_html=snap.allocation.to_html(index=False, float_format="%.2f") if not snap.allocation.empty else "",
                positions_html=snap.positions.to_html(index=False, float_format="%.2f") if not snap.positions.empty else "",
            )
        )

        xlsx_path = base.with_suffix(".xlsx")
        self.build_xlsx(snap, xlsx_path)

        return {"csv": str(csv_path), "html": str(html_path), "xlsx": str(xlsx_path)}

    @staticmethod
    def build_xlsx(snap: PortfolioSnapshot, path: Path, extras: dict | None = None) -> Path:
        """Multi-sheet Excel report. `extras` is an optional dict of {sheet_name: DataFrame}."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(path, engine="xlsxwriter") as xw:
            summary_df = pd.DataFrame(list(snap.summary.items()), columns=["metric", "value"])
            summary_df.to_excel(xw, sheet_name="Summary", index=False)
            if not snap.holdings.empty:
                snap.holdings.to_excel(xw, sheet_name="Holdings", index=False)
            if not snap.allocation.empty:
                snap.allocation.to_excel(xw, sheet_name="Allocation", index=False)
            if not snap.positions.empty:
                snap.positions.to_excel(xw, sheet_name="Positions", index=False)
            for sheet, df in (extras or {}).items():
                if isinstance(df, pd.DataFrame) and not df.empty:
                    df.to_excel(xw, sheet_name=sheet[:31], index=False)

            wb = xw.book
            money = wb.add_format({"num_format": "#,##0.00"})
            pct = wb.add_format({"num_format": "0.00%"})
            for ws in xw.sheets.values():
                ws.set_column(0, 30, 16, money)
        return path

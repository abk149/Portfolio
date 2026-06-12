"""PDF downloader + section-aware extractor + LLM summariser.

Designed for NSE/BSE filings and annual reports. We avoid pushing the full
PDF (often 100+ pages) to an 8B model — instead we slice to the Executive
Summary, MD&A, and Financial Statements pages by keyword detection, and
summarise that.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import requests

from config import settings
from src.utils.logger import get_logger

log = get_logger("tools.pdf")

SECTION_HINTS = [
    "executive summary", "management discussion", "md&a",
    "financial highlights", "balance sheet", "profit and loss",
    "cash flow", "auditor", "risk factor",
]


class PDFExtractor:
    def __init__(self, llm=None):
        self.llm = llm  # optional LLMProvider for summarisation
        self.dir = settings.cache_dir / "pdfs"
        self.dir.mkdir(parents=True, exist_ok=True)

    # ---------- download ----------
    def download(self, url: str, name: Optional[str] = None) -> Path:
        name = name or url.split("/")[-1].split("?")[0]
        if not name.endswith(".pdf"):
            name += ".pdf"
        path = self.dir / name
        if path.exists() and path.stat().st_size > 0:
            return path
        log.info(f"Downloading {url}")
        r = requests.get(url, timeout=60,
                         headers={"User-Agent": "Mozilla/5.0 D-R1-Quant"})
        r.raise_for_status()
        path.write_bytes(r.content)
        return path

    # ---------- extract ----------
    def to_text(self, path: Path) -> str:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return "\n".join((p.extract_text() or "") for p in reader.pages)

    def slice_sections(self, text: str, max_chars: int = 18000) -> str:
        """Keep only paragraphs near keyword hits — keeps PDFs feedable to 8B."""
        lines = text.split("\n")
        keep = []
        idx = 0
        while idx < len(lines):
            line = lines[idx]
            low = line.lower()
            if any(h in low for h in SECTION_HINTS):
                # take 60 lines around the hit
                keep.extend(lines[max(0, idx - 5): idx + 60])
                idx += 60
            else:
                idx += 1
        out = "\n".join(keep)
        if len(out) < 2000:  # nothing matched → fall back to first chunk
            out = text[:max_chars]
        return out[:max_chars]

    # ---------- summarise via LLM ----------
    def summarise(self, path: Path, focus: str = "balance sheet health") -> dict:
        text = self.slice_sections(self.to_text(path))
        if not self.llm:
            return {"raw_excerpt": text[:5000]}

        prompt = (
            f"You are a quant analyst. Extract ONLY verifiable facts from this filing.\n"
            f"Focus: {focus}.\n"
            f"Return ONE JSON object with keys: debt_to_equity, current_ratio, "
            f"free_cash_flow_cr, promoter_pledging_pct, revenue_growth_pct, "
            f"earnings_growth_pct, key_risks (list of short strings), notes.\n"
            f"Use null if not stated. NEVER invent numbers.\n\n"
            f"--- FILING EXCERPT ---\n{text}\n--- END ---"
        )
        # use the LLM's plain chat path (no tool loop needed)
        try:
            answer = self.llm.tool_loop(
                system="You return only valid JSON. No prose.",
                user=prompt, tools=[], dispatch=lambda *a, **k: None, max_turns=1,
            )
            import json
            m = re.search(r"\{[\s\S]*\}", answer)
            return json.loads(m.group(0)) if m else {"error": "no JSON in output",
                                                     "raw": answer[:1000]}
        except Exception as e:
            return {"error": str(e)}

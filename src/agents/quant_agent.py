"""D-R1-Quant — the multi-stage funnel orchestrator.

Pipeline:
  Stage 0 — Macro mode (VIX, PCR, USD/INR)            → bias the funnel
  Stage 1 — Technical bulk screen (TA-Lib)             → top 20 candidates
  Stage 2 — Intelligence agent (web + PDF + LLM)       → 10 validated names
  Stage 3 — MPT max-Sharpe optimisation                → target weights
  Stage 4 — Intraday advisor (VWAP/OI/ATR)             → entry/exit alerts

The class is built as a small state-machine — each stage is a node that
reads/writes a shared `QuantState` and emits an `agent_logs` row for every
decision (TimescaleDB or JSONL fallback). Reasoning <think> blocks from the
local DeepSeek-R1 are persisted by the TraceRecorder for future fine-tuning.

This is NOT LangGraph — but the model is identical (stateful graph of
nodes), without dragging in the dep.
"""
from __future__ import annotations

# ── import tracing ───────────────────────────────────────────────────────
# Every line below prints a timestamped marker BEFORE the import runs, so a
# silent hang lights up the culprit in the terminal. Cheap once warm-cached.
import sys
import time as _time

_T0 = _time.time()
def _imp(msg):
    print(f"[IMPORT +{_time.time()-_T0:5.2f}s] quant_agent: {msg}",
          flush=True, file=sys.stderr)

_imp("stdlib")
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

_imp("pandas")
import pandas as pd

_imp("src.agents.base")
from src.agents.base import BaseAgent
_imp("src.db")
from src.db import agent_log, get_db
_imp("src.intraday")
from src.intraday import IntradayScanner
_imp("src.llm")
from src.llm import get_llm
_imp("src.screener.talib_screener")
from src.screener.talib_screener import TALibScreener
_imp("src.tools (web, pdf, macro, quant, upstox)")
from src.tools import MacroSnapshot, PDFExtractor, QuantCalculator, WebSearcher
_imp("src.training")
from src.training import TraceRecorder
_imp("src.utils.logger")
from src.utils.logger import get_logger
_imp("src.vector")
from src.vector import VectorStore
_imp("done")

log = get_logger("d-r1-quant")


def _pct(v):
    """ROE is stored as a decimal (0.18) in the KB; the prompt wants % (18)."""
    if v is None:
        return None
    try:
        v = float(v)
        return round(v * 100, 2) if abs(v) <= 5 else round(v, 2)
    except (ValueError, TypeError):
        return None


class _NullTracer:
    """No-op fallback when TraceRecorder can't be created."""
    path = "(disabled)"
    def record(self, *a, **k): pass
    def record_step(self, *a, **k): pass
    def to_lora_jsonl(self, *a, **k): return None


# ---------------- shared state ----------------
@dataclass
class QuantState:
    run_id: str
    universe: str = "nifty50"
    macro: dict = field(default_factory=dict)
    stage1: pd.DataFrame = field(default_factory=pd.DataFrame)   # technical candidates
    stage2: list[dict] = field(default_factory=list)             # validated dossiers
    stage3: dict = field(default_factory=dict)                   # MPT result
    stage4: list[dict] = field(default_factory=list)             # intraday alerts
    errors: list[str] = field(default_factory=list)


# ---------------- the Stage-2 sub-agent ----------------
class IntelligenceAgent(BaseAgent):
    """Validates a single ticker. Uses tools to fetch news + filings, then
    emits a JSON health card. We keep this as an LLM agent because the work
    is genuinely fuzzy (parsing prose, deciding what counts as a red flag)."""

    SYSTEM = (
        "You are an equity analyst for the Indian market. For one ticker:\n"
        "1. Call `fundamentals` ONCE for P/E, ROE, D/E, growth from screener.in.\n"
        "2. Call `news` ONCE for recent context from Indian outlets.\n"
        "3. (Optional) Call `market_snapshot` if you need OHLC / 52w range.\n"
        "4. Reason inside <think>...</think>.\n"
        "5. Return ONE JSON object DIRECTLY (no 'answer' wrapper):\n"
        "   {\"verdict\": \"KEEP\"|\"REJECT\", \"health_score\": 0-100, "
        "\"pe\": number_or_null, \"roe_pct\": number_or_null, "
        "\"debt_to_equity\": number_or_null, "
        "\"key_risks\": [string], \"thesis\": \"one sentence\"}\n\n"
        "REJECT rules:\n"
        " • D/E > 1.5 (banks excepted),\n"
        " • ROE < 8% AND sales growth < 0 (no quality, no growth),\n"
        " • News mentions fraud / default / regulatory action,\n"
        " • Price at multi-year low with no catalyst.\n"
        "KEEP if quality (ROE > 12%), positive growth, neutral/positive news.\n"
        "Do NOT call the same tool twice. After 3 tool calls, you MUST answer."
    )

    def __init__(self, vec, macro: dict | None = None):
        super().__init__()
        self.web = WebSearcher(vector_store=vec)
        self.macro = macro or {}
        self.TOOLS = [
            {"name": "fundamentals",
             "description": "P/E, ROE, D/E, EPS growth, sales growth, market cap, sector — "
                            "scraped live from screener.in. Call ONCE.",
             "input_schema": {"type": "object",
                              "properties": {"symbol": {"type": "string",
                                  "description": "NSE trading symbol e.g. RELIANCE, HDFCBANK"}},
                              "required": ["symbol"]}},
            {"name": "news",
             "description": "Recent news + sentiment from moneycontrol/ET/livemint/business-standard. "
                            "Call ONCE.",
             "input_schema": {"type": "object",
                              "properties": {"stock": {"type": "string"}},
                              "required": ["stock"]}},
            {"name": "market_snapshot",
             "description": "OHLC, last price, 52-week high/low, volume from Upstox. "
                            "OPTIONAL — only call if you specifically need price-action context.",
             "input_schema": {"type": "object",
                              "properties": {"symbol": {"type": "string"}},
                              "required": ["symbol"]}},
        ]

    def _dispatch(self, name, kwargs):
        sym = (kwargs.get("symbol") or kwargs.get("stock")
               or kwargs.get("yf_ticker", "").replace(".NS", "")).upper()
        if name == "news":
            return self.web.news_for(sym)
        if name == "fundamentals":
            from src.tools.screener_in import fetch_fundamentals
            data = fetch_fundamentals(sym)
            if not data:
                return {"error": f"no screener.in entry for {sym} (may be unlisted/very small cap)"}
            return data
        if name == "market_snapshot":
            from src.data import MarketData
            return MarketData().fundamentals(f"{sym}.NS")
        raise ValueError(name)


# ---------------- the orchestrator ----------------
class DR1QuantAgent:
    def __init__(self, universe: str = "nifty50", live_orders: bool = False):
        import os
        import sys
        def _p(m):
            print(f"[AGENT __init__] {m}", flush=True, file=sys.stderr)

        _p("state")
        self.state = QuantState(run_id=uuid.uuid4().hex[:8], universe=universe)
        self.live_orders = live_orders

        _p("TraceRecorder")
        try:
            self.tracer = TraceRecorder(self.state.run_id)
        except Exception as e:
            log.warning(f"TraceRecorder init failed (continuing without traces): {e}")
            self.tracer = _NullTracer()

        # ChromaDB downloads a HuggingFace embedder once (~90 MB). After
        # that startup is instant. Enabled by default — set DR1_USE_VECTOR=0
        # in .env to disable.
        if os.getenv("DR1_USE_VECTOR", "1") != "0":
            _p("VectorStore (RAG enabled)")
            try:
                log.info("Opening ChromaDB vector store …")
                self.vec = VectorStore("news")
                log.info("Vector store ready — news will be ingested for retrieval.")
            except Exception as e:
                log.warning(f"VectorStore unavailable: {e}")
                self.vec = None
        else:
            _p("VectorStore disabled (DR1_USE_VECTOR=0)")
            self.vec = None
        _p("ready")

    # ---------- Stage 0 ----------
    def stage0_macro(self) -> dict:
        log.info("Stage 0 — macro snapshot")
        m = MacroSnapshot().market_mode()
        self.state.macro = m
        get_db().log_macro(m)
        agent_log(self.state.run_id, "stage0", "macro", "snapshot",
                  None, mode=m["mode"], vix=m.get("india_vix"),
                  pcr=m.get("nifty_pcr"))
        return m

    # ---------- Stage 1 ----------
    def stage1_technical(self, top_n: int = 20) -> pd.DataFrame:
        log.info("Stage 1 — TA-Lib bulk screen")
        df = TALibScreener().scan(self.state.universe, top_n=top_n)
        self.state.stage1 = df
        agent_log(self.state.run_id, "stage1", "talib", "scan",
                  None, candidates=int(len(df)),
                  symbols=df["symbol"].tolist() if not df.empty else [])
        return df

    # ---------- Stage 2 ----------
    # ---------- Stage 2: deterministic gather → ONE LLM call per stock ----------
    # The previous design ran an agentic tool-loop per stock (3-4 Ollama
    # generations each). That heated the M4 hard. New design:
    #   • Gather news + screener.in fundamentals + Upstox snapshot in
    #     plain Python (no LLM)
    #   • Bundle everything into one prompt
    #   • Make ONE llm.complete() call → verdict JSON
    UNIFIED_SYSTEM = (
        "You are an equity analyst for the Indian market. ALL relevant data "
        "for one stock has been given to you in a single shot. Reason inside "
        "<think>...</think> then return ONE JSON object directly (no 'answer' "
        "wrapper):\n"
        "{\"verdict\": \"KEEP\"|\"REJECT\", \"health_score\": 0-100, "
        "\"pe\": number_or_null, \"roe_pct\": number_or_null, "
        "\"debt_to_equity\": number_or_null, "
        "\"sales_growth_pct\": number_or_null, "
        "\"profit_growth_pct\": number_or_null, "
        "\"sector\": string_or_null, "
        "\"key_risks\": [string], \"thesis\": \"one sentence\"}\n\n"
        "CRITICAL: the prompt's `KEY METRICS` block already lists the values. "
        "If a value is shown (not 'n/a'), you MUST copy it into the corresponding "
        "field of the JSON verdict — do NOT emit null for a value that was "
        "given. Use null ONLY when the input shows 'n/a'.\n\n"
        "REJECT rules:\n"
        "  • D/E > 1.5 (banks/NBFCs excepted),\n"
        "  • ROE < 8% AND sales growth < 0,\n"
        "  • News mentions fraud / default / regulatory action,\n"
        "  • Price at multi-year low with no catalyst,\n"
        "  • Data too thin to justify a position.\n"
        "KEEP if quality (ROE > 12% OR strong growth), neutral/positive news, "
        "and the technical setup is intact."
    )

    @property
    def llm(self):
        if not hasattr(self, "_llm"):
            from src.llm import get_llm
            self._llm = get_llm()
        return self._llm

    def _gather_stock_data(self, row: dict) -> dict:
        """Pure Python, no LLM. Returns everything Stage 2 needs.

        Fundamentals are read from the KB universe store FIRST (populated by
        the Universe Map ingestion engine). Only if the KB has nothing — or
        the entry is stale — do we fetch live. This is the KB-as-cache design.
        """
        sym = row["symbol"]
        bundle: dict = {"technical": {
            "setups": row.get("setups", []),
            "score": row.get("score"),
            "ltp": row.get("ltp"),
        }}

        # --- fundamentals: KB-first, BUT only if KB entry actually has data ---
        fundamentals = None
        try:
            from src.kb import KnowledgeBase
            kb = KnowledgeBase.get()
            kb_age = kb.stock_age_days(sym)
            if kb_age is not None and kb_age < 14:
                stored = kb.get_stock(sym) or {}
                # A KB entry that's "fresh" but stores only NaN-y fundamentals
                # is worthless. Require at least one real ratio before trusting it.
                has_real_data = any(
                    stored.get(k) not in (None, "")
                    for k in ("PE", "ROE", "DE")
                )
                if has_real_data:
                    fundamentals = {
                        "pe": stored.get("PE"),
                        "roe_pct": _pct(stored.get("ROE")),
                        "debt_to_equity": stored.get("DE"),
                        "sector": stored.get("sector"),
                        "industry": stored.get("industry"),
                        "market_cap_cr": stored.get("market_cap_cr"),
                        "fund_score": stored.get("fund_score"),
                        "_source": f"KB (age {kb_age:.1f}d)",
                    }
                    log.info(f"  fundamentals: KB cache hit "
                             f"(PE={stored.get('PE')} ROE={stored.get('ROE')})")
                else:
                    log.info(f"  fundamentals: KB entry has no ratios — "
                             f"refetching live")
        except Exception as e:
            log.debug(f"KB read for {sym} failed: {e}")

        if fundamentals is None:
            try:
                from src.tools.screener_in import fetch_fundamentals
                fundamentals = fetch_fundamentals(sym) or {}
                fundamentals["_source"] = "live fetch"
                log.info(
                    f"  fundamentals: live → PE={fundamentals.get('pe')} "
                    f"ROE={fundamentals.get('roe_pct')} "
                    f"D/E={fundamentals.get('debt_to_equity')} "
                    f"sources={fundamentals.get('_sources')}"
                )
            except Exception as e:
                fundamentals = {"_error": str(e)}
        bundle["fundamentals"] = fundamentals

        # --- market snapshot: always live (it's cheap, Upstox quote) ---
        try:
            from src.data import MarketData
            bundle["market_snapshot"] = MarketData().fundamentals(row["yf_ticker"]) or {}
        except Exception as e:
            bundle["market_snapshot"] = {"_error": str(e)}

        # --- wikipedia: free company description, always reachable ---
        bundle["wiki"] = {}
        try:
            from src.tools.wikipedia import wiki_summary
            name = (row.get("name")
                    or (fundamentals or {}).get("company_name")
                    or sym)
            bundle["wiki"] = wiki_summary(name) or {}
            if bundle["wiki"].get("extract"):
                log.info(f"  wikipedia ✓ {bundle['wiki'].get('title')}")
        except Exception as e:
            log.debug(f"wikipedia for {sym}: {e}")

        # --- news: multi-source (DDG + Reddit + NSE filings) WITH bodies ---
        try:
            from src.tools.web_search import WebSearcher
            company_name = (row.get("name")
                            or (fundamentals or {}).get("company_name"))
            bundle["news"] = WebSearcher(vector_store=self.vec) \
                .news_with_bodies(sym, company_name)
        except Exception as e:
            log.warning(f"news fetch for {sym} failed: {e}")
            bundle["news"] = []

        # --- primary-source filings: multi-source (screener.in + NSE),
        #     download + extract + ingest into KB ---
        bundle["filings"] = []
        try:
            from src.tools.document_fetcher import fetch_documents_multisource
            bundle["filings"] = fetch_documents_multisource(
                sym, fundamentals=fundamentals, max_docs=2, ingest_kb=True)
            log.info(f"  filings: {len(bundle['filings'])} ingested for {sym}")
        except Exception as e:
            log.warning(f"document fetch for {sym} failed: {e}")

        return bundle

    def _build_unified_prompt(self, row: dict, data: dict) -> str:
        m = self.state.macro
        f = data.get("fundamentals") or {}

        # Surface the KEY metrics as an explicit key-value block at the top.
        # When we just dumped JSON the model often returned `null` for fields
        # that WERE present in the dict — being lazy about parsing. Spelled
        # out plainly it tends to copy them into the verdict.
        def _val(x):
            return "n/a" if x in (None, "", "null") else x
        key_metrics = (
            f"  P/E             : {_val(f.get('pe'))}\n"
            f"  ROE (%)         : {_val(f.get('roe_pct'))}\n"
            f"  Debt/Equity     : {_val(f.get('debt_to_equity'))}\n"
            f"  Sales growth %  : {_val(f.get('sales_growth_pct'))}\n"
            f"  Profit growth % : {_val(f.get('profit_growth_pct'))}\n"
            f"  Market cap (cr) : {_val(f.get('market_cap_cr'))}\n"
            f"  Sector          : {_val(f.get('sector'))}\n"
            f"  Industry        : {_val(f.get('industry'))}\n"
            f"  Sources         : {_val(f.get('_sources') or f.get('_source'))}\n"
        )

        sections: list[str] = [
            f"Stock: {row['symbol']} ({row['name']})\n"
            f"Trading symbol: {row['symbol']}  ·  yf: {row['yf_ticker']}\n",
            f"=== KEY METRICS (USE THESE IN YOUR VERDICT JSON) ===\n{key_metrics}",
            f"=== TECHNICAL ===\n{json.dumps(data['technical'], default=str)}\n",
            f"=== FUNDAMENTALS (raw — for context) ===\n"
            f"{json.dumps(data['fundamentals'], default=str)[:1500]}\n",
            f"=== MARKET SNAPSHOT (Upstox) ===\n"
            f"{json.dumps(data['market_snapshot'], default=str)[:800]}\n",
        ]

        wiki = data.get("wiki") or {}
        if wiki.get("extract"):
            sections.append(
                f"=== COMPANY (Wikipedia) ===\n"
                f"{wiki.get('description', '')}\n{wiki['extract'][:800]}\n"
            )

        # News — emit titles + snippets, AND article bodies where we got them.
        news = data.get("news", [])
        if news:
            news_lines = []
            for n in news[:7]:
                line = f"- [{n.get('source', '?')}] {n.get('title', '?')}"
                snip = (n.get("snippet") or "").strip()
                if snip:
                    line += f"\n    {snip[:240]}"
                body = (n.get("body") or "").strip()
                if body:
                    line += f"\n    EXCERPT: {body[:1200]}"
                news_lines.append(line)
            sections.append("=== RECENT NEWS & DISCUSSION ===\n"
                            + "\n".join(news_lines) + "\n")

        filings = data.get("filings", [])
        if filings:
            sections.append(
                "=== PRIMARY-SOURCE FILINGS (NSE/BSE) ===\n"
                + "\n\n".join(
                    f"[{f.get('title','filing')[:100]}]\n{(f.get('text') or '')[:2500]}"
                    for f in filings
                ) + "\n"
            )

        sections.append(
            f"=== MACRO ===\nIndia VIX={m.get('india_vix')}, "
            f"Nifty PCR={m.get('nifty_pcr')}, "
            f"Nifty today={m.get('nifty_change_pct')}%, "
            f"mode={m.get('mode', '?')}\n"
        )
        sections.append("Analyse and return the verdict JSON.")
        return "\n".join(sections)

    def stage2_intelligence(self) -> list[dict]:
        log.info("Stage 2 — unified analysis (1 LLM call per stock)")
        if self.state.stage1.empty:
            return []

        out = []
        total = len(self.state.stage1)
        for idx, row in enumerate(self.state.stage1.to_dict("records"), 1):
            sym = row["symbol"]
            log.info(f"[{idx}/{total}] gathering data for {sym} ({row['name']}) …")
            data = self._gather_stock_data(row)
            prompt = self._build_unified_prompt(row, data)

            log.info(f"[{idx}/{total}] → single LLM call …")
            try:
                ans = self.llm.complete(self.UNIFIED_SYSTEM, prompt)
            except Exception as e:
                ans = json.dumps({"verdict": "REJECT", "error": str(e)})

            self.tracer.record_step(prompt, ans)
            card = self._parse_json(ans)
            card["symbol"] = sym
            card["yf_ticker"] = row["yf_ticker"]
            card["instrument_key"] = row["instrument_key"]
            out.append(card)

            verdict = card.get("verdict", "?")
            log.info(f"      → {verdict} (health {card.get('health_score', '?')})")

            _payload = {
                k: v for k, v in card.items()
                if k not in ("symbol", "run_id", "stage", "actor", "action")
                and not isinstance(v, (dict, list))
            }
            agent_log(self.state.run_id, "stage2", "unified", "verdict",
                      symbol=sym, **_payload)

            if any(k in card for k in ["debt_to_equity", "pe", "roe_pct"]):
                get_db().upsert_fundamentals(sym, card, source="unified_stage2")

            try:
                from src.kb import KnowledgeBase
                KnowledgeBase.get().record_decision(
                    symbol=sym, prompt=prompt, response=ans, verdict=verdict,
                    meta={
                        "run_id": self.state.run_id,
                        "macro_mode": self.state.macro.get("mode"),
                        "vix": self.state.macro.get("india_vix"),
                        "tech_score": row.get("score"),
                        "setups": ",".join(row.get("setups", [])),
                        "health_score": card.get("health_score"),
                    },
                )
            except Exception as e:
                log.debug(f"decision capture failed: {e}")

        self.state.stage2 = out
        log.info(f"Stage 2 done — {sum(1 for c in out if c.get('verdict')=='KEEP')} KEEP / "
                 f"{sum(1 for c in out if c.get('verdict')=='REJECT')} REJECT")
        return out

    # ---------- Stage 3 ----------
    def stage3_optimise(self, max_weight: float = 0.25) -> dict:
        log.info("Stage 3 — MPT optimisation")
        keep = [c for c in self.state.stage2 if c.get("verdict") == "KEEP"]
        if len(keep) < 2:
            self.state.stage3 = {"error": f"only {len(keep)} KEEP names — need ≥ 2"}
            return self.state.stage3
        yf_tickers = [c["yf_ticker"] for c in keep]
        res = QuantCalculator().max_sharpe(yf_tickers, max_weight=max_weight)
        self.state.stage3 = res
        agent_log(self.state.run_id, "stage3", "mpt", "max_sharpe",
                  None, **{k: v for k, v in res.items() if not isinstance(v, dict)})
        return res

    # ---------- Stage 4 ----------
    def stage4_intraday(self) -> list[dict]:
        log.info("Stage 4 — intraday advisor")
        if not self.state.stage3 or "weights_pct" not in self.state.stage3:
            log.info("Stage 4 skipped — no optimal portfolio from Stage 3")
            return []
        # Scan ONLY the portfolio's tickers — not the whole universe
        symbols = list(self.state.stage3["weights_pct"].keys())
        log.info(f"Stage 4 — evaluating {len(symbols)} portfolio tickers for intraday setups")
        df = IntradayScanner().scan_tickers(symbols, min_score=40)
        rows = df.to_dict("records") if not df.empty else []
        for r in rows:
            _payload = {
                k: v for k, v in r.items()
                if k not in ("symbol", "run_id", "stage", "actor", "action")
                and not isinstance(v, (list, dict))
            }
            agent_log(self.state.run_id, "stage4", "intraday", "alert",
                      symbol=r["symbol"], **_payload)
        self.state.stage4 = rows
        return rows

    # ---------- Driver ----------
    def run(self) -> dict:
        import sys
        def _p(m):
            print(f"[FUNNEL] {m}", flush=True, file=sys.stderr)

        _p("→ Stage 0 (macro)")
        self.stage0_macro()
        _p("← Stage 0 done")

        _p("→ Stage 1 (technical)")
        self.stage1_technical()
        _p(f"← Stage 1 done ({len(self.state.stage1)} candidates)")

        _p("→ Stage 2 (intelligence agent — slow)")
        self.stage2_intelligence()
        _p(f"← Stage 2 done ({len(self.state.stage2)} verdicts)")

        _p("→ Stage 3 (MPT)")
        self.stage3_optimise()
        _p("← Stage 3 done")

        _p("→ Stage 4 (intraday)")
        self.stage4_intraday()
        _p(f"← Stage 4 done ({len(self.state.stage4)} alerts)")

        _p("✓ Full funnel complete")
        return self.summary()

    def summary(self) -> dict:
        return {
            "run_id": self.state.run_id,
            "macro": self.state.macro,
            "candidates": int(len(self.state.stage1)),
            "validated": [c for c in self.state.stage2 if c.get("verdict") == "KEEP"],
            "rejected": [c.get("symbol") for c in self.state.stage2
                         if c.get("verdict") == "REJECT"],
            "portfolio": self.state.stage3,
            "intraday_alerts": self.state.stage4,
            "trace_file": str(self.tracer.path),
        }

    # ---------- helpers ----------
    @staticmethod
    def _parse_json(text: str) -> dict:
        import re
        m = re.search(r"\{[\s\S]*\}", re.sub(r"<think>[\s\S]*?</think>", "", text))
        if not m:
            return {"verdict": "REJECT", "error": "no JSON in agent response",
                    "raw": text[:1000]}
        try:
            return json.loads(m.group(0))
        except Exception as e:
            return {"verdict": "REJECT", "error": f"bad JSON: {e}",
                    "raw": m.group(0)[:1000]}

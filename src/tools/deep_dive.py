"""Per-stock deep dive: drill into the last two quarters' results + valuation,
surface issues, and compute a quant-based 'good entry' price.

Pipeline:
  1. Fundamentals (screener.in: PE/ROE/ROCE/D-E/growth/mcap).
  2. Recent filings — results / earnings-call PDFs (NSE/BSE), text-extracted.
  3. Quant entry model from price history (DMAs, support, RSI, ATR zone).
  4. LLM equity-research synthesis: financial health, issues, red flags,
     valuation read, and a view on the entry zone. STRICT JSON.

Everything is best-effort; a failing source never breaks the dive.
"""
from __future__ import annotations

from typing import Optional

from src.utils.logger import get_logger

log = get_logger("deep_dive")


def _entry_price(symbol: str) -> dict:
    """Quant 'good entry' zone from price action (technical)."""
    try:
        from src.data import MarketData
        from src.utils.indicators import sma, rsi, atr
        df = MarketData().daily(f"{symbol}.NS", None, lookback_days=420)
        if df is None or df.empty or len(df) < 60:
            return {"note": "insufficient price history for an entry model"}
        close = df["close"].astype(float)
        cur = float(close.iloc[-1])
        dma50 = float(sma(close, 50).iloc[-1])
        dma200 = float(sma(close, 200).iloc[-1]) if len(df) >= 200 else None
        low60 = float(close.tail(60).min())
        high60 = float(close.tail(60).max())
        r = float(rsi(close, 14).iloc[-1])
        try:
            a = float(atr(df, 14).iloc[-1])
        except Exception:
            a = cur * 0.015
        # Nearest meaningful support at/below current price.
        supports = sorted([s for s in (dma50, dma200, low60)
                           if s and s <= cur * 1.02], reverse=True)
        support = supports[0] if supports else low60
        buf = a or cur * 0.015
        entry_low = round(support - buf, 2)
        entry_high = round(min(cur, support + buf), 2)
        suggested = round(min(cur, support + buf * 0.4), 2)
        disc = round((cur - suggested) / cur * 100, 1) if cur else 0.0
        if cur > dma50:
            note = (f"Uptrend (above 50-DMA). Prefer a pullback into ₹{entry_low}–{entry_high} "
                    f"(~50-DMA/support) rather than chasing at ₹{round(cur,2)}.")
        elif dma200 and cur > dma200:
            note = f"Mid-trend; accumulation zone ₹{entry_low}–{entry_high} near support."
        else:
            note = f"Below 200-DMA — weak trend; only a base near ₹{entry_low}–{entry_high} is buyable."
        return {
            "current": round(cur, 2), "dma50": round(dma50, 2),
            "dma200": round(dma200, 2) if dma200 else None,
            "low_60d": round(low60, 2), "high_60d": round(high60, 2),
            "rsi": round(r, 1), "atr": round(buf, 2), "support": round(support, 2),
            "entry_low": entry_low, "entry_high": entry_high,
            "suggested_entry": suggested, "discount_to_cmp_pct": disc, "note": note,
        }
    except Exception as e:
        log.debug(f"entry model failed for {symbol}: {e}")
        return {"note": f"entry model error: {e}"}


def deep_dive(symbol: str, max_docs: int = 3) -> dict:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"error": "no symbol"}

    fund = {}
    try:
        from src.tools.screener_in import fetch_fundamentals
        fund = fetch_fundamentals(symbol) or {}
    except Exception as e:
        log.debug(f"fundamentals failed for {symbol}: {e}")

    docs: list[dict] = []
    try:
        from src.tools.document_fetcher import fetch_documents_multisource
        docs = fetch_documents_multisource(symbol, fundamentals=fund,
                                           max_docs=max_docs, ingest_kb=True) or []
    except Exception as e:
        log.debug(f"docs failed for {symbol}: {e}")

    entry = _entry_price(symbol)

    excerpts = "\n\n".join(
        f"[{d.get('title')}]\n{(d.get('text') or '')[:3000]}"
        for d in docs[:max_docs] if d.get("text"))

    system = (
        "You are a skeptical sell-side equity research analyst. Drill into the "
        "last TWO quarters of results and the valuation. Surface REAL issues in "
        "the financial numbers (margins, debt, cash flow, receivables, one-offs, "
        "guidance) — do not be promotional. If the filings text is missing, say so "
        "and reason from the fundamentals provided. Output STRICT JSON only.")
    prompt = f"""STOCK: {symbol}

FUNDAMENTALS:
PE {fund.get('pe')} · ROE {fund.get('roe_pct')}% · ROCE {fund.get('roce_pct')}% ·
D/E {fund.get('debt_to_equity')} · sales growth {fund.get('sales_growth_pct')}% ·
profit growth {fund.get('profit_growth_pct')}% · mcap {fund.get('market_cap_cr')} cr ·
sector {fund.get('sector')}

QUANT ENTRY (technical): CMP {entry.get('current')} · 50-DMA {entry.get('dma50')} ·
200-DMA {entry.get('dma200')} · RSI {entry.get('rsi')} · suggested entry
{entry.get('suggested_entry')} (zone {entry.get('entry_low')}–{entry.get('entry_high')})

RECENT FILINGS / RESULTS (text excerpts):
{excerpts or '(no filing text retrieved — reason from fundamentals)'}

Return STRICT JSON, no prose:
{{"financial_health":"2-3 sentences","issues":["concrete issue from the numbers", "..."],
"valuation":"cheap|fair|expensive — one line why","red_flags":["..."],
"quarter_trend":"how the last 2 quarters trended (revenue/margin/profit)",
"entry_view":"is the quant entry zone reasonable given fundamentals?","verdict":"BUY|ACCUMULATE|HOLD|AVOID with one line"}}"""

    analysis = None
    try:
        from src.llm import get_llm
        from src.llm.ollama_provider import _extract_json
        reply = get_llm().complete(system, prompt) or ""
        analysis = _extract_json(reply) or {"raw": reply[:800]}
    except Exception as e:
        analysis = {"error": f"LLM analysis failed: {e}"}

    return {
        "symbol": symbol,
        "fundamentals": {k: fund.get(k) for k in (
            "pe", "roe_pct", "roce_pct", "debt_to_equity", "sales_growth_pct",
            "profit_growth_pct", "market_cap_cr", "sector")},
        "entry": entry,
        "analysis": analysis,
        "sources": [{"title": d.get("title"), "url": d.get("url")} for d in docs],
    }

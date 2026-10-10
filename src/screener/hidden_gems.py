"""Hidden Gems — stocks that have NOT had their bull run yet, but are starting
to show it.

The existing screener answers "what is strong right now". This engine answers
a different question: what has been going NOWHERE for months — a long sideways
base, well off its 52-week high, lagging the index — while quietly showing the
two things that precede a run:

  (a) technical inflection  — accumulation (OBV rising while price is flat),
      volatility contraction, volume returning on up days, relative strength
      turning up, the first higher high;
  (b) fundamental inflection — earnings and revenue growing, ROE respectable,
      balance sheet not stretched.

Every candidate carries the explicit list of which signals fired (and which
didn't), so the UI never has to say "trust the score". The summary is
template-built from those signals — no LLM — so it works offline on-device.

Compat: pandas 2.1 (Chaquopy) and current pandas; numpy + pandas only.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from src.data import MarketData
from src.data.instruments import resolve_universe
from src.utils.indicators import ema, macd, atr
from src.utils.logger import get_logger

log = get_logger("hidden_gems")

ENGINE = "hidden_gems"

# Verdict thresholds on the 0-100 gem score.
VERDICTS = ((75.0, "STRONG_GEM"), (60.0, "GEM"), (45.0, "WATCH"))

# How a verdict reads in the ghost queue, which every engine shares.
_CONVICTION = {"STRONG_GEM": "HIGH", "GEM": "MEDIUM", "WATCH": "LOW"}

# Block weights when combining into gem_score.
W_BASE, W_TECH, W_FUND = 0.35, 0.40, 0.25


class GemConfig:
    """Every threshold in one place, so the API/CLI can override and tests can
    pin them. Defaults are the product thesis, not magic numbers."""

    def __init__(
        self,
        min_bars: int = 200,             # need ~10 months of daily history
        min_avg_volume: float = 50_000,  # liquidity floor: avg daily shares (21d)
        near_high_reject_pct: float = -3.0,   # within 3% of 52w high → already ran
        freefall_depth_pct: float = -50.0,    # >50% below high …
        max_run_12m_pct: float = 30.0,        # … or already up >30% in 12m → not hidden
        base_min_months: float = 4.0,    # sweet spot for base length …
        base_max_months: float = 24.0,
        lookback_days: int = 800,        # ~3y of candles so a 24m base is visible
        benchmark: str = "^NSEI",
    ):
        self.min_bars = min_bars
        self.min_avg_volume = min_avg_volume
        self.near_high_reject_pct = near_high_reject_pct
        self.freefall_depth_pct = freefall_depth_pct
        self.max_run_12m_pct = max_run_12m_pct
        self.base_min_months = base_min_months
        self.base_max_months = base_max_months
        self.lookback_days = lookback_days
        self.benchmark = benchmark


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _f(x, nd=2):
    """float-or-None, rounded, never NaN/Inf — the API contract is strict JSON."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if np.isnan(v) or np.isinf(v):
        return None
    return round(v, nd)


def _sig(key, label, value, passed, weight, detail):
    """One scored signal. passed=None means 'couldn't test' — it is excluded
    from the denominator, never silently counted either way."""
    return {"key": key, "label": label, "value": _f(value) if isinstance(value, (int, float, np.floating, np.integer)) else value,
            "passed": (None if passed is None else bool(passed)),
            "weight": float(weight), "detail": detail}


def _band_score(x, lo, hi, taper=None):
    """1.0 inside [lo,hi], linearly tapering to 0 over `taper` outside."""
    if x is None:
        return None
    if lo <= x <= hi:
        return 1.0
    taper = taper or (hi - lo) or 1.0
    if x < lo:
        return max(0.0, 1.0 - (lo - x) / taper)
    return max(0.0, 1.0 - (x - hi) / taper)


def _slope(y: np.ndarray) -> float:
    """Normalised linear slope of a series (per-bar, as a fraction of its mean).
    Scale-free, so OBV and price slopes are comparable."""
    y = np.asarray(y, dtype=float)
    if len(y) < 3 or not np.isfinite(y).all():
        return 0.0
    x = np.arange(len(y), dtype=float)
    b = np.polyfit(x, y, 1)[0]
    m = abs(np.mean(y)) or 1.0
    return float(b / m)


# ---------------------------------------------------------------------------
# the core: evaluate ONE stock's daily frame (+ optional index + fundamentals)
# ---------------------------------------------------------------------------
def evaluate(df: pd.DataFrame, index_close: Optional[pd.Series] = None,
             fundamentals: Optional[dict] = None, cfg: Optional[GemConfig] = None,
             symbol: str = "", name: str = "") -> dict:
    """Score one stock. Returns {"ok": True, ...candidate...} or
    {"ok": False, "reason": ...}. Pure function — no I/O, fully deterministic."""
    cfg = cfg or GemConfig()
    if df is None or df.empty or len(df) < cfg.min_bars:
        return {"ok": False, "reason": f"insufficient history ({0 if df is None else len(df)} bars)"}
    for col in ("close", "high", "low", "volume"):
        if col not in df.columns:
            return {"ok": False, "reason": f"missing column '{col}'"}

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    vol = df["volume"].astype(float)
    last = float(close.iloc[-1])
    if not np.isfinite(last) or last <= 0:
        return {"ok": False, "reason": "no usable last price"}

    # ---------------- base / "hasn't run" measurements ----------------
    # The reference high/low reaches back up to ~2 years (504 bars), not just
    # the trailing 252: the thesis explicitly rewards bases of 4–24 months, and
    # a 20-month base's starting high is by definition older than one year.
    tail = close.tail(504)
    high_52w = float(tail.max())
    low_52w = float(tail.min())
    dist_high_pct = (last / high_52w - 1.0) * 100.0
    dist_low_pct = (last / low_52w - 1.0) * 100.0

    # bars since the 52w high was printed → base length in months
    at_high = (tail >= high_52w).to_numpy()
    bars_since_high = int(np.argmax(at_high[::-1]))
    base_months = bars_since_high / 21.0

    # deepest point of the base (drawdown from the high)
    since_high = low.tail(bars_since_high + 1)
    depth_pct = (float(since_high.min()) / high_52w - 1.0) * 100.0 if len(since_high) else None

    ret_6m = _f(close.pct_change(126).iloc[-1] * 100) if len(close) > 126 else None
    ret_12m = _f(close.pct_change(252).iloc[-1] * 100) if len(close) > 252 else None

    e50, e200 = ema(close, 50), ema(close, 200)
    e200_falling = bool(e200.iloc[-1] < e200.iloc[-21]) if len(e200) > 21 else False

    # ---------------- hard gates ----------------
    avg_vol_21 = float(vol.tail(21).mean())
    if np.isfinite(avg_vol_21) and avg_vol_21 < cfg.min_avg_volume:
        return {"ok": False, "reason": f"illiquid — avg daily volume {avg_vol_21:,.0f} < {cfg.min_avg_volume:,.0f}"}
    if dist_high_pct >= cfg.near_high_reject_pct:
        return {"ok": False, "reason": f"within {abs(cfg.near_high_reject_pct):.0f}% of its 52-week high — the run already happened"}
    if ret_12m is not None and ret_12m > cfg.max_run_12m_pct:
        return {"ok": False, "reason": f"already up {ret_12m:.0f}% in 12 months — not hidden"}
    if dist_high_pct <= cfg.freefall_depth_pct and e200_falling:
        return {"ok": False, "reason": (f"{dist_high_pct:.0f}% below its high with a falling 200EMA — "
                                        "freefall, not a base (value-trap risk)")}

    # ---------------- volatility contraction (VCP-style tightening) ----------------
    a = atr(df, 14)
    atr_now = float(a.tail(21).mean()) / last
    # compare against the EARLIER base (~6–12 months back), not the recent one
    if len(a) > 252:
        atr_base = float(a.iloc[-252:-126].mean()) / last
    elif len(a) > 126:
        atr_base = float(a.iloc[-126:-42].mean()) / last
    else:
        atr_base = None
    vol_contraction = (atr_now / atr_base) if (atr_base and atr_base > 0) else None

    # ---------------- base block score (0-100) ----------------
    base_parts = [
        (_band_score(base_months, cfg.base_min_months, cfg.base_max_months, taper=6.0), 30),
        (_band_score(dist_high_pct, -40.0, -5.0, taper=10.0), 30),
        # hasn't run: flat-to-modest 12m return is ideal; up to the gate tapering
        (_band_score(ret_12m if ret_12m is not None else ret_6m, -25.0, 15.0, taper=15.0), 20),
        # contraction: ≤0.7 great, ≥1.1 none
        (None if vol_contraction is None else max(0.0, min(1.0, (1.1 - vol_contraction) / 0.4)), 20),
    ]
    used = [(s, w) for s, w in base_parts if s is not None]
    base_score = (sum(s * w for s, w in used) / sum(w for _, w in used)) if used else None

    # ---------------- accumulation / technical inflection signals ----------------
    m = macd(close)
    win = min(63, len(close) - 1)

    # OBV rising while price flat. Slope is in units of average daily volume
    # per bar (dimensionless), so it's comparable across stocks and histories.
    direction = np.sign(close.diff().fillna(0.0))
    obv = (direction * vol).cumsum()
    obv_raw = float(np.polyfit(np.arange(win, dtype=float),
                               obv.tail(win).to_numpy(), 1)[0])
    vol_avg = float(vol.tail(win).mean()) or 1.0
    obv_slope = obv_raw / vol_avg
    px_slope = _slope(close.tail(win).to_numpy())
    obv_pass = bool(obv_slope > 0.1 and abs(px_slope) < 0.004)
    t_sigs = [_sig("obv_divergence", "Accumulation: OBV rising while price flat",
                   obv_slope, obv_pass, 2.0,
                   f"OBV {obv_slope:+.2f}× avg volume/bar vs price {px_slope*100:+.2f}%/bar over {win} sessions")]

    # volume dry-up in the late base (excluding the last ~10 sessions, so the
    # expansion itself doesn't contaminate the "dry" average), then expansion
    # on recent up days
    late_dry = float(vol.iloc[-win:-10].mean()) if len(vol) > win + 10 else None
    early_base = float(vol.iloc[-3 * win:-win].mean()) if len(vol) > 3 * win else None
    recent_close = close.tail(11)
    up_days = recent_close.diff().fillna(0.0) >= 0
    up_vol = vol.tail(11)[up_days.to_numpy()]
    up_vol_avg = float(up_vol.mean()) if len(up_vol) else None
    dry_up = (late_dry is not None and early_base is not None and early_base > 0
              and late_dry < early_base * 0.9)
    expansion = bool(up_vol_avg is not None and late_dry and up_vol_avg > late_dry * 1.5)
    vol_ratio = (up_vol_avg / late_dry) if (up_vol_avg and late_dry) else None
    t_sigs.append(_sig("volume_pattern", "Volume dry-up in base, expansion on up days",
                       vol_ratio, bool(dry_up and expansion), 1.5,
                       ("volume dried up in the late base; " if dry_up else "no dry-up; ")
                       + (f"up-day volume {vol_ratio:.1f}× the dry-base average"
                          if vol_ratio else "up-day volume n/a")))

    # relative strength vs index turning up while price still in base
    rs_now = rs_then = None
    if index_close is not None and len(index_close) > 84 and len(close) > 84:
        idx = index_close.astype(float)
        s3 = float(close.pct_change(63).iloc[-1])
        i3 = float(idx.pct_change(63).iloc[-1])
        s3_then = float(close.pct_change(63).iloc[-22])
        i3_then = float(idx.pct_change(63).iloc[-22])
        rs_now = (s3 - i3) * 100.0
        rs_then = (s3_then - i3_then) * 100.0
    rs_pass = (None if rs_now is None else bool(rs_now > rs_then))
    t_sigs.append(_sig("relative_strength", "Relative strength vs index turning up",
                       rs_now, rs_pass, 2.0,
                       ("index data unavailable" if rs_now is None else
                        f"3m RS {rs_now:+.1f}pp vs {rs_then:+.1f}pp a month ago")))

    # price reclaiming a rising 50EMA / 200EMA flattening or turning up
    e50_rising = bool(e50.iloc[-1] > e50.iloc[-21]) if len(e50) > 21 else False
    e200_slope = (float(e200.iloc[-1]) / float(e200.iloc[-21]) - 1.0) if len(e200) > 21 and e200.iloc[-21] else None
    ema_pass = bool(last > e50.iloc[-1] and e50_rising and (e200_slope is None or e200_slope > -0.02))
    t_sigs.append(_sig("ema_reclaim", "Price above a rising 50EMA, 200EMA flattening",
                       _f((last / float(e50.iloc[-1]) - 1) * 100), ema_pass, 1.5,
                       f"price {((last / float(e50.iloc[-1])) - 1) * 100:+.1f}% vs 50EMA "
                       f"({'rising' if e50_rising else 'falling'}), 200EMA 1m {('n/a' if e200_slope is None else f'{e200_slope*100:+.1f}%')}"))

    # first higher-high structure: recent high exceeds the prior base high
    if len(high) > 84:
        recent_high = float(high.tail(21).max())
        prior_high = float(high.iloc[-84:-21].max())
        hh_pass = bool(recent_high > prior_high)
        hh_val = (recent_high / prior_high - 1) * 100
    else:
        hh_pass, hh_val = None, None
    t_sigs.append(_sig("higher_high", "First higher high above the base",
                       hh_val, hh_pass, 1.0,
                       ("not enough history" if hh_val is None else
                        f"recent 1m high {hh_val:+.1f}% vs prior 3m high")))

    # MACD crossing up near/above zero after a long stretch below it
    macd_line, sig_line = m["macd"], m["signal"]
    below_zero_share = float((macd_line.tail(126) < 0).mean()) if len(macd_line) > 126 else 0.0
    crossed_up = bool(macd_line.iloc[-1] > sig_line.iloc[-1]
                      and (macd_line.tail(21) <= sig_line.tail(21)).any())
    near_zero = bool(abs(float(macd_line.iloc[-1])) <= last * 0.02)  # within ~2% of price
    macd_pass = bool(crossed_up and near_zero and below_zero_share > 0.6)
    t_sigs.append(_sig("macd_turn", "MACD crossing up near zero after long below",
                       _f(macd_line.iloc[-1]), macd_pass, 1.5,
                       f"MACD {'crossed up' if crossed_up else 'no fresh cross'}, "
                       f"{below_zero_share*100:.0f}% of the last 6m spent below zero"))

    tech_used = [s for s in t_sigs if s["passed"] is not None]
    tech_score = (sum(s["weight"] for s in tech_used if s["passed"])
                  / sum(s["weight"] for s in tech_used)) if tech_used else None

    # ---------------- fundamental inflection signals ----------------
    f = fundamentals or {}
    pe = f.get("pe")
    roe = f.get("roe_pct")
    de = f.get("debt_to_equity")
    sg = f.get("sales_growth_pct")
    pg = f.get("profit_growth_pct")
    fcf = f.get("free_cashflow") or f.get("freeCashflow")

    f_sigs = [
        _sig("earnings_growth", "Earnings growing (YoY profit growth > 0)",
             pg, (None if pg is None else bool(pg > 0)), 2.0,
             "profit growth unavailable" if pg is None else f"profit growth {pg:+.1f}% YoY"),
        _sig("revenue_growth", "Revenue growing ≥ 10%",
             sg, (None if sg is None else bool(sg >= 10)), 1.5,
             "sales growth unavailable" if sg is None else f"sales growth {sg:+.1f}% YoY"),
        _sig("roe", "ROE ≥ 12%",
             roe, (None if roe is None else bool(roe >= 12)), 2.0,
             "ROE unavailable" if roe is None else f"ROE {roe:.1f}%"),
        _sig("leverage", "Debt-to-equity ≤ 1.5 (lower is better)",
             de, (None if de is None else bool(de <= 1.5)), 1.5,
             "D/E unavailable" if de is None else f"D/E {de:.2f}"),
        _sig("cashflow", "Positive free cash flow",
             None, (None if fcf is None else bool(fcf > 0)), 1.0,
             "FCF unavailable" if fcf is None else ("FCF positive" if fcf > 0 else "FCF negative")),
    ]
    f_used = [s for s in f_sigs if s["passed"] is not None]
    if f_used:
        raw = sum(s["weight"] for s in f_used if s["passed"]) / sum(s["weight"] for s in f_used)
        coverage = sum(s["weight"] for s in f_used) / sum(s["weight"] for s in f_sigs)
        # same philosophy as screener.fundamental: partial data may not inflate
        fund_score = raw * (coverage ** 0.5)
    else:
        fund_score, coverage = None, 0.0

    # ---------------- combine ----------------
    blocks = [(base_score, W_BASE), (tech_score, W_TECH), (fund_score, W_FUND)]
    present = [(s, w) for s, w in blocks if s is not None]
    if not present:
        return {"ok": False, "reason": "no scorable signals"}
    gem_score = round(100.0 * sum(s * w for s, w in present) / sum(w for _, w in present), 1)
    verdict = next((v for thr, v in VERDICTS if gem_score >= thr), "SKIP")

    base_score_r = round(base_score * 100, 1) if base_score is not None else None
    tech_score_r = round(tech_score * 100, 1) if tech_score is not None else None
    fund_score_r = round(fund_score * 100, 1) if fund_score is not None else None

    candidate = {
        "symbol": symbol, "name": name or symbol,
        "sector": f.get("sector"),
        "price": _f(last),
        "gem_score": gem_score, "verdict": verdict,
        "base": {
            "months": _f(base_months, 1),
            "depth_pct": _f(depth_pct, 1),
            "volatility_contraction": _f(vol_contraction, 2),
        },
        "distance_from_52w_high_pct": _f(dist_high_pct, 1),
        "distance_from_52w_low_pct": _f(dist_low_pct, 1),
        "return_6m_pct": ret_6m,
        "return_12m_pct": ret_12m,
        "relative_strength_3m": _f(rs_now, 1),
        "technical_signals": t_sigs,
        "fundamental_signals": f_sigs,
        "block_scores": {"base": base_score_r, "technical": tech_score_r,
                         "fundamental": fund_score_r,
                         "fundamental_coverage": round(coverage, 2)},
        "summary": "",   # filled below
    }
    candidate["summary"] = _summary(candidate)
    return {"ok": True, **candidate}


def _summary(c: dict) -> str:
    """Template thesis from the signals that actually fired. No LLM."""
    b = c["base"]
    parts = []
    if b.get("months") is not None and c.get("distance_from_52w_high_pct") is not None:
        parts.append(f"{b['months']:.0f}-month base, {abs(c['distance_from_52w_high_pct']):.0f}% below its 52-week high")
    vc = b.get("volatility_contraction")
    if vc is not None and vc < 0.9:
        parts.append(f"volatility contracting ({vc:.2f}× earlier base)")
    fired_t = [s["label"] for s in c["technical_signals"] if s["passed"]]
    fired_f = [s["label"] for s in c["fundamental_signals"] if s["passed"]]

    def _lc(label: str) -> str:
        # lowercase the first letter for mid-sentence use, but not acronyms
        return label if label[:2].isupper() else label[0].lower() + label[1:]

    if fired_t:
        parts.append("technicals: " + "; ".join(_lc(l) for l in fired_t[:3]))
    if fired_f:
        parts.append("fundamentals: " + "; ".join(_lc(l) for l in fired_f[:3]))
    if not parts:
        return "Base forming, but few inflection signals have fired yet."
    return ("Hasn't run — " + parts[0] + ". " + ". ".join(p[0].upper() + p[1:] for p in parts[1:]) + ".")


def queue_gems(gems: list[dict], run_id: Optional[str] = None) -> dict:
    """Put gem picks in the ghost queue, alongside every other engine's.

    Idempotent per scan day: a pick that is already queued — or that you have
    since bought or dismissed — is left alone rather than resurrected.
    """
    try:
        from src.portfolio import recommendations as rec
        return rec.record([
            {"symbol": g["symbol"], "sector": g.get("sector"),
             # The queue speaks HIGH/MEDIUM/LOW like every other engine;
             # the gem's own verdict stays in the rationale.
             "conviction": _CONVICTION.get(g.get("verdict"), "LOW"),
             "rationale": (f"{str(g.get('verdict') or 'gem').replace('_', ' ').title()} "
                           f"(gem score {float(g.get('gem_score') or 0):.0f}). "
                           f"{g.get('summary') or ''}").strip(),
             "suggested_entry": g.get("price")}
            for g in gems if g.get("symbol")
        ], ENGINE, run_id=run_id)
    except Exception as e:
        log.warning(f"could not queue gem recommendations: {e}")
        return {"added": 0, "error": str(e)}


# ---------------------------------------------------------------------------
# the engine: universe → candidates → gate → recommendations
# ---------------------------------------------------------------------------
class HiddenGemsEngine:
    """Mirrors ScreenerEngine's shape: broker → MarketData with Yahoo fallback,
    thread pool over the universe, fundamentals via screener.in → Yahoo."""

    def __init__(self, upstox=None, workers: int = 2, cfg: Optional[GemConfig] = None):
        from src.brokers import get_broker
        try:
            self.upstox = upstox or get_broker()
        except Exception:
            log.warning("No broker session — hidden-gems using yfinance only.")
            self.upstox = None
        self.md = MarketData(self.upstox)
        self.workers = workers
        self.cfg = cfg or GemConfig()

    # ---------- data ----------
    def _index_close(self) -> Optional[pd.Series]:
        try:
            df = self.md.daily(self.cfg.benchmark, None, lookback_days=self.cfg.lookback_days)
            if df is not None and not df.empty and len(df) > 84:
                return df["close"]
        except Exception as e:
            log.debug(f"benchmark fetch failed ({e}) — RS signal will be 'unknown'")
        return None

    def _fundamentals(self, sym: str) -> dict:
        """screener.in → Yahoo fallback, same pattern as ScreenerEngine."""
        raw: dict = {}
        try:
            from src.tools.screener_in import fetch_fundamentals
            raw = fetch_fundamentals(sym) or {}
        except Exception as e:
            log.debug(f"screener.in failed {sym}: {e}")
        has_ratios = any(raw.get(k) is not None
                         for k in ("pe", "roe_pct", "debt_to_equity", "market_cap_cr"))
        if not has_ratios:
            try:
                from src.tools.yahoo_fundamentals import fetch_fundamentals_yahoo
                yahoo = fetch_fundamentals_yahoo(sym) or {}
                raw = {**raw, **{k: v for k, v in yahoo.items() if v is not None}}
            except Exception as e:
                log.debug(f"yahoo fundamentals failed {sym}: {e}")
        return raw

    # ---------- one row ----------
    def _row(self, name, yf_t, nse_t, ikey, index_close) -> Optional[dict]:
        try:
            df = self.md.daily(yf_t, ikey, lookback_days=self.cfg.lookback_days)
            res = evaluate(df, index_close=index_close, fundamentals=None,
                           cfg=self.cfg, symbol=nse_t, name=str(name))
            if not res.get("ok"):
                log.debug(f"✗ {nse_t}: {res.get('reason')}")
                return None
            fund = self._fundamentals(nse_t)
            # re-evaluate with fundamentals so the score/verdict include them
            res = evaluate(df, index_close=index_close, fundamentals=fund,
                           cfg=self.cfg, symbol=nse_t, name=str(name))
            if not res.get("ok"):
                return None
            res["_fundamentals"] = fund
            return res
        except Exception as e:
            log.debug(f"gem scan failed {name}: {e}")
            return None

    # ---------- public ----------
    def scan(self, universe: str = "nifty50", min_score: float = 45.0,
             max_results: int = 25, record: bool = True) -> dict:
        t0 = time.time()
        items = resolve_universe(universe)
        log.info(f"Hidden-gems scan: {len(items)} instruments in '{universe}'")
        index_close = self._index_close()

        rows: list[dict] = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = [ex.submit(self._row, *it, index_close) for it in items]
            for f in as_completed(futs):
                r = f.result()
                if r:
                    rows.append(r)

        # The uniform gate: every engine's candidates pass the same tests.
        from src.portfolio.screening import uniform_gate
        gems: list[dict] = []
        for r in rows:
            rw = {"stance": "move ahead",
                  "reading": (f"{abs(r['distance_from_52w_high_pct'] or 0):.0f}% below its "
                              "52-week high after a long base — the move is ahead, not made"),
                  "runway_score": r["gem_score"]}
            gate = uniform_gate(r["symbol"], fundamentals=r.get("_fundamentals"), runway=rw)
            if not gate.get("passed"):
                log.info(f"✗ {r['symbol']} blocked by uniform gate: {gate.get('summary')}")
                continue
            r.pop("_fundamentals", None)
            r.pop("ok", None)            # internal flag, not part of the API contract
            gems.append(r)

        gems = [g for g in gems if g["gem_score"] >= min_score]
        gems.sort(key=lambda g: -g["gem_score"])
        gems = gems[:max_results]

        result = {
            "gems": gems,
            "scanned": len(items),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "elapsed_sec": round(time.time() - t0, 2),
        }

        if record and gems:
            queue_gems(gems, run_id=result["generated_at"][:10])
        log.info(f"Hidden-gems: {len(gems)} gems from {len(items)} scanned "
                 f"in {result['elapsed_sec']}s")
        return result

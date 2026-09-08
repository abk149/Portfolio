"""Ghost portfolio — paper positions, treated exactly like real ones.

The point is to test the system's recommendations before trusting them with
money. You "invest" in a suggestion, it books at the prevailing price, and from
then on it is tracked, valued and reviewed with the same machinery as the real
book: the same price feed, the same technicals, the same calendar.

Three things this deliberately does NOT do:

  * It does not book at a price you type in. Entries use the live quote at the
    moment you press invest, because a paper record that lets you pick your fill
    is worthless as a test of the system.
  * It does not quietly forget. Positions are stored on disk and survive
    restarts, so an honest track record accumulates.
  * It does not flatter itself. Closed positions keep their realised P&L, so the
    total includes trades that went wrong, not just the open ones that went well.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from config import settings
from src.utils.logger import get_logger

log = get_logger("portfolio.ghost")

_LOCK = threading.Lock()


def _store_path():
    p = settings.cache_dir / "ghost_portfolio.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict:
    p = _store_path()
    if not p.exists():
        return {"positions": [], "created_at": datetime.now().isoformat(timespec="seconds")}
    try:
        return json.loads(p.read_text())
    except Exception as e:
        log.warning(f"ghost store unreadable ({e}) — starting a fresh one")
        return {"positions": [], "created_at": datetime.now().isoformat(timespec="seconds")}


def _save(data: dict) -> None:
    """Atomic write — a half-written file would lose the whole track record."""
    p = _store_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str))
    tmp.replace(p)


def _yf(symbol: str) -> str:
    s = (symbol or "").upper().strip().replace(".NS", "").replace(".BO", "")
    return f"{s}.NS"


def _price(symbol: str) -> Optional[float]:
    """Live price, broker first then the free public source."""
    try:
        from src.data import MarketData
        px = MarketData().ltp(_yf(symbol))
        if px:
            return float(px)
    except Exception as e:
        log.debug(f"ghost price via MarketData failed for {symbol}: {e}")
    try:
        from src.data import yahoo
        px = yahoo.ltp(_yf(symbol))
        return float(px) if px else None
    except Exception as e:
        log.debug(f"ghost price via yahoo failed for {symbol}: {e}")
        return None


# ---------------------------------------------------------------------------
# Trading
# ---------------------------------------------------------------------------
def buy(symbol: str, amount: Optional[float] = None, qty: Optional[int] = None,
        note: str = "", source: str = "manual") -> dict:
    """Open a paper position at the prevailing price."""
    symbol = (symbol or "").upper().strip().replace(".NS", "").replace(".BO", "")
    if not symbol:
        return {"error": "No symbol given."}

    px = _price(symbol)
    if not px or px <= 0:
        return {"error": f"Couldn't get a live price for {symbol}, so there is no "
                         f"honest entry to book. Try again in a moment."}

    if qty is None:
        if not amount or amount <= 0:
            return {"error": "Give an amount to invest, or a share count."}
        qty = int(amount // px)
        if qty < 1:
            return {"error": f"₹{amount:,.0f} doesn't buy a single share of "
                             f"{symbol} at ₹{px:,.2f}."}

    pos = {
        "id": uuid.uuid4().hex[:10],
        "symbol": symbol,
        "qty": int(qty),
        "entry_price": round(px, 2),
        "entry_date": date.today().isoformat(),
        "entry_ts": datetime.now().isoformat(timespec="seconds"),
        "invested": round(px * qty, 2),
        "note": note,
        "source": source,          # where the idea came from (ideas / quant / manual)
        "status": "open",
    }
    with _LOCK:
        data = _load()
        data["positions"].append(pos)
        _save(data)
    log.info(f"ghost BUY {symbol} x{qty} @ {px}")
    return {"ok": True, "position": pos}


def sell(position_id: str) -> dict:
    """Close a paper position at the prevailing price, keeping the realised P&L."""
    with _LOCK:
        data = _load()
        for pos in data["positions"]:
            if pos["id"] == position_id and pos["status"] == "open":
                px = _price(pos["symbol"])
                if not px:
                    return {"error": f"No live price for {pos['symbol']} right now."}
                pos["status"] = "closed"
                pos["exit_price"] = round(px, 2)
                pos["exit_date"] = date.today().isoformat()
                pos["realised_pnl"] = round((px - pos["entry_price"]) * pos["qty"], 2)
                pos["realised_pnl_pct"] = round(
                    (px / pos["entry_price"] - 1) * 100, 2)
                _save(data)
                log.info(f"ghost SELL {pos['symbol']} @ {px} "
                         f"→ {pos['realised_pnl']}")
                return {"ok": True, "position": pos}
    return {"error": "Position not found, or already closed."}


def reset() -> dict:
    with _LOCK:
        _save({"positions": [],
               "created_at": datetime.now().isoformat(timespec="seconds")})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Valuation
# ---------------------------------------------------------------------------
def snapshot() -> dict:
    """Current state: open positions marked to market, plus realised history."""
    data = _load()
    positions = data.get("positions", [])
    open_rows, closed_rows = [], []
    invested = current = realised = 0.0

    for pos in positions:
        if pos["status"] == "closed":
            closed_rows.append({
                "id": pos["id"], "symbol": pos["symbol"], "qty": pos["qty"],
                "entry_price": pos["entry_price"], "exit_price": pos.get("exit_price"),
                "entry_date": pos["entry_date"], "exit_date": pos.get("exit_date"),
                "pnl": pos.get("realised_pnl"), "pnl_pct": pos.get("realised_pnl_pct"),
                "source": pos.get("source"),
            })
            realised += float(pos.get("realised_pnl") or 0)
            continue

        px = _price(pos["symbol"])
        value = (px or pos["entry_price"]) * pos["qty"]
        pnl = value - pos["invested"]
        invested += pos["invested"]
        current += value
        open_rows.append({
            "id": pos["id"], "symbol": pos["symbol"], "qty": pos["qty"],
            "entry_price": pos["entry_price"], "last_price": round(px, 2) if px else None,
            "invested": round(pos["invested"], 2), "value": round(value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / pos["invested"] * 100, 2) if pos["invested"] else 0.0,
            "entry_date": pos["entry_date"],
            "held_days": (date.today() - date.fromisoformat(pos["entry_date"])).days,
            "source": pos.get("source"), "note": pos.get("note"),
            "priced": px is not None,
        })

    unrealised = current - invested
    return {
        "created_at": data.get("created_at"),
        "open": sorted(open_rows, key=lambda r: -r["value"]),
        "closed": sorted(closed_rows, key=lambda r: r.get("exit_date") or "", reverse=True),
        "summary": {
            "n_open": len(open_rows), "n_closed": len(closed_rows),
            "invested": round(invested, 2),
            "current_value": round(current, 2),
            "unrealised_pnl": round(unrealised, 2),
            "unrealised_pnl_pct": round(unrealised / invested * 100, 2) if invested else 0.0,
            "realised_pnl": round(realised, 2),
            "total_pnl": round(unrealised + realised, 2),
        },
    }


def _price_history(symbols: list[str], since: date) -> dict[str, pd.Series]:
    """Daily closes per symbol from `since`, on a tz-naive normalised index."""
    out: dict[str, pd.Series] = {}
    lookback = max((date.today() - since).days + 10, 30)
    for sym in symbols:
        df = pd.DataFrame()
        try:
            from src.data import MarketData
            df = MarketData().daily(_yf(sym), None, lookback_days=lookback)
        except Exception as e:
            log.debug(f"ghost history via MarketData failed {sym}: {e}")
        if df is None or df.empty:
            try:
                from src.data import yahoo
                df = yahoo.daily(_yf(sym), lookback)
            except Exception as e:
                log.debug(f"ghost history via yahoo failed {sym}: {e}")
        if df is None or df.empty or "close" not in df:
            continue
        s = pd.to_numeric(df["close"], errors="coerce").dropna()
        idx = pd.to_datetime(s.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_convert("Asia/Kolkata").tz_localize(None)
        s.index = idx.normalize()
        out[sym] = s[~s.index.duplicated(keep="last")].sort_index()
    return out


def equity_curve() -> dict:
    """Day-by-day value of the ghost book since its first position.

    A position contributes only from its entry date, so the curve reflects when
    each idea was actually taken — not a backfilled "what if I'd always held it",
    which would be a different and much flattering question.
    """
    data = _load()
    positions = data.get("positions", [])
    if not positions:
        return {"points": [], "note": "No ghost positions yet."}

    first = min(date.fromisoformat(p["entry_date"]) for p in positions)
    symbols = sorted({p["symbol"] for p in positions})
    hist = _price_history(symbols, first)
    if not hist:
        return {"points": [], "error": "Couldn't load price history for the "
                                       "ghost holdings."}

    all_dates = sorted({d for s in hist.values() for d in s.index
                        if d.date() >= first})
    rows = []
    for d in all_dates:
        value = invested = realised = 0.0
        for p in positions:
            entry = date.fromisoformat(p["entry_date"])
            if entry > d.date():
                continue
            s = hist.get(p["symbol"])
            if s is None:
                continue
            valid = s[s.index <= d]
            if valid.empty:
                continue
            px = float(valid.iloc[-1])
            if p["status"] == "closed" and p.get("exit_date") and \
                    date.fromisoformat(p["exit_date"]) <= d.date():
                realised += float(p.get("realised_pnl") or 0)
                continue                      # no longer held on this date
            value += px * p["qty"]
            invested += p["invested"]
        rows.append({
            "date": d.strftime("%Y-%m-%d"),
            "value": round(value, 2),
            "invested": round(invested, 2),
            "pnl": round(value - invested + realised, 2),
        })
    return {"points": rows, "symbols": symbols,
            "since": first.isoformat()}


def combined_curve(real_equity_curve: Optional[list[dict]] = None) -> dict:
    """The real book and the ghost book on one timeline.

    `combined` is what the portfolio would be worth if the paper ideas had been
    taken for real alongside the actual holdings; `total_pnl` adds the ghost
    result to the real one.
    """
    ghost = equity_curve()
    gpoints = {p["date"]: p for p in ghost.get("points", [])}

    real = real_equity_curve or []
    rows = []
    for r in real:
        d = str(r.get("date"))[:10]
        pv = float(r.get("portfolio_value") or 0)
        inv = float(r.get("invested_capital") or 0)
        g = gpoints.get(d)
        gv = float(g["value"]) if g else 0.0
        gpnl = float(g["pnl"]) if g else 0.0
        rows.append({
            "date": d,
            "portfolio": round(pv, 2),
            "ghost": round(gv, 2),
            "combined": round(pv + gv, 2),
            "real_pnl": round(pv - inv, 2),
            "total_pnl": round((pv - inv) + gpnl, 2),
        })

    # If performance hasn't been run, the ghost curve still stands alone.
    if not rows and ghost.get("points"):
        rows = [{"date": p["date"], "portfolio": 0.0, "ghost": p["value"],
                 "combined": p["value"], "real_pnl": 0.0, "total_pnl": p["pnl"]}
                for p in ghost["points"]]

    return {
        "points": rows,
        "ghost": ghost,
        "has_real": bool(real),
        "note": ("Ghost positions are added from the date you took them, so the "
                 "combined line shows what your book would have done had you "
                 "acted on each idea when the system made it — not a backfilled "
                 "best case."),
    }


# ---------------------------------------------------------------------------
# Sell discipline — the same review a real position would get
# ---------------------------------------------------------------------------
def _position_signals(pos: dict, hist: pd.Series) -> dict:
    """Deterministic exit signals for one holding.

    Computed in Python rather than asked of the model, so they are reproducible
    and explainable: the model gets these as evidence, it doesn't invent them.
    """
    from src.utils.indicators import atr, rsi, sma

    out: dict = {"symbol": pos["symbol"], "id": pos["id"], "flags": [], "reasons": []}
    if hist is None or hist.empty or len(hist) < 20:
        out["reasons"].append("Not enough price history to judge — holding by default.")
        return out

    close = hist.astype(float)
    cur = float(close.iloc[-1])
    entry = float(pos["entry_price"])
    gain_pct = (cur / entry - 1) * 100
    out["current"] = round(cur, 2)
    out["gain_pct"] = round(gain_pct, 2)

    r = float(rsi(close, 14).iloc[-1]) if len(close) >= 15 else None
    d50 = float(sma(close, 50).iloc[-1]) if len(close) >= 50 else None
    d200 = float(sma(close, 200).iloc[-1]) if len(close) >= 200 else None
    out.update(rsi=round(r, 1) if r else None,
               dma50=round(d50, 2) if d50 else None,
               dma200=round(d200, 2) if d200 else None)

    # Peak since entry — a big give-back is different from never having gained.
    since = close[close.index >= pd.Timestamp(pos["entry_date"])]
    if not since.empty:
        peak = float(since.max())
        out["peak_since_entry"] = round(peak, 2)
        out["drawdown_from_peak_pct"] = round((cur / peak - 1) * 100, 2)

    if r is not None and r > 75 and gain_pct > 10:
        out["flags"].append("OVERBOUGHT")
        out["reasons"].append(f"RSI {r:.0f} with a {gain_pct:.0f}% gain — stretched.")
    if gain_pct >= 25:
        out["flags"].append("TAKE_PROFIT")
        out["reasons"].append(f"Up {gain_pct:.0f}% — worth banking part of it.")
    if gain_pct <= -12:
        out["flags"].append("STOP")
        out["reasons"].append(f"Down {abs(gain_pct):.0f}% — past a sensible stop.")
    if d50 and cur < d50 and gain_pct > 0:
        out["flags"].append("TREND_BREAK")
        out["reasons"].append("Price has lost its 50-day average while still in profit.")
    if d200 and cur < d200:
        out["flags"].append("BELOW_200DMA")
        out["reasons"].append("Trading below the 200-day average — the long-term "
                              "trend is against this.")
    if out.get("drawdown_from_peak_pct", 0) <= -15:
        out["flags"].append("GIVING_BACK")
        out["reasons"].append(f"Given back {abs(out['drawdown_from_peak_pct']):.0f}% "
                              f"from its high since you bought.")
    held = (date.today() - date.fromisoformat(pos["entry_date"])).days
    if held > 90 and abs(gain_pct) < 3:
        out["flags"].append("DEAD_MONEY")
        out["reasons"].append(f"Held {held} days and gone nowhere — the capital "
                              f"could be working elsewhere.")

    try:
        a = float(atr(hist.to_frame("close").assign(high=close, low=close), 14).iloc[-1])
        out["atr_stop"] = round(cur - 2 * a, 2)
    except Exception:
        pass

    if not out["flags"]:
        out["reasons"].append("Nothing in the price action argues for selling.")
    return out


def signals(calendar: Optional[dict] = None) -> dict:
    """Exit review for every open ghost position, with event risk attached."""
    snap = snapshot()
    positions = [p for p in _load().get("positions", []) if p["status"] == "open"]
    if not positions:
        return {"positions": [], "note": "No open ghost positions to review."}

    first = min(date.fromisoformat(p["entry_date"]) for p in positions)
    hist = _price_history(sorted({p["symbol"] for p in positions}), first - timedelta(days=420))

    upcoming = []
    if calendar:
        today = calendar.get("today", date.today().isoformat())
        upcoming = [e for e in (calendar.get("events") or [])
                    if e.get("date", "") >= today
                    and e.get("importance") == "HIGH"][:6]

    rows = []
    for p in positions:
        sig = _position_signals(p, hist.get(p["symbol"]))
        sig["qty"] = p["qty"]
        sig["entry_price"] = p["entry_price"]
        sig["entry_date"] = p["entry_date"]
        # Market-wide events hit every holding; naming them per position keeps
        # the review actionable rather than a footnote.
        sig["events"] = [{"date": e["date"], "title": e["title"],
                          "importance": e["importance"]} for e in upcoming[:3]]
        rows.append(sig)

    macro = {}
    try:
        from src.tools.macro import MacroSnapshot
        macro = MacroSnapshot().market_mode()
    except Exception as e:
        log.debug(f"ghost review macro failed: {e}")

    return {
        "positions": rows,
        "macro": macro,
        "upcoming_events": upcoming,
        "summary": snap["summary"],
        "flagged": [r["symbol"] for r in rows if r["flags"]],
    }

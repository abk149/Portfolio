"""Turn a rupee allocation into whole shares you can actually place.

An optimiser works in continuous weights, so it happily returns ₹400 for a
stock trading at ₹600 — a number you cannot act on. Worse, it looks like a
recommendation.

So the allocation is converted to WHOLE SHARES at the live price, and anything
that can't buy at least one is reported as unaffordable rather than shown as a
tiny rupee figure. Flooring frees cash, so one greedy pass re-spends it on the
names the optimiser weighted highest.

Deliberately not chasing the last rupee: the goal is a placeable basket that
stays within budget, not perfect utilisation. Leftover cash is reported, not
hidden.
"""
from __future__ import annotations

import math

from typing import Callable, Optional

from src.utils.logger import get_logger

log = get_logger("portfolio.sizing")


def to_whole_shares(
    buys: list[dict],
    price_of: Callable[[str], Optional[float]],
    cash: float,
    top_up: bool = True,
) -> dict:
    """Convert continuous rupee allocations into a placeable basket.

    `buys` are the optimiser's rows: {ticker, buy_inr, final_weight_pct, ...}.
    `price_of(ticker)` returns a live price, or None.

    Returns {allocations, unaffordable, unpriced, totals}.
    """
    priced: list[dict] = []
    unpriced: list[dict] = []

    for b in buys or []:
        ticker = b.get("ticker") or ""
        symbol = ticker.replace(".NS", "").replace(".BO", "")
        target = float(b.get("buy_inr") or 0)
        px = None
        try:
            px = price_of(ticker)
        except Exception as e:
            log.debug(f"price lookup failed for {ticker}: {e}")
        if not px or px <= 0:
            # No price means no honest share count. Say so rather than
            # inventing one from a stale figure.
            unpriced.append({"symbol": symbol, "target_amount": round(target, 0),
                             "reason": "no live price available right now"})
            continue
        priced.append({
            "symbol": symbol, "ticker": ticker, "price": round(float(px), 2),
            "target_amount": round(target, 0),
            "weight_pct": b.get("final_weight_pct"),
            "shares": int(target // px),
            # Never more than one share beyond what the optimiser asked for.
            # Without this cap, spare cash piles onto whichever name happens to
            # be cheapest and the basket stops reflecting the weights that were
            # computed — a 1% idea can quietly become a 12% position.
            "max_shares": math.ceil(target / px),
        })

    # Highest-weight names get first refusal on the freed cash.
    priced.sort(key=lambda r: -(r.get("weight_pct") or 0))

    spent = sum(r["shares"] * r["price"] for r in priced)

    # Staying within budget must be unconditional. deploy_cash normally returns
    # targets summing to the cash, but nothing here should depend on a caller
    # being well-behaved — so if the floored basket is already over, trim from
    # the LOWEST-weight names until it fits.
    if spent > cash:
        for r in sorted(priced, key=lambda x: (x.get("weight_pct") or 0)):
            while r["shares"] > 0 and spent > cash:
                r["shares"] -= 1
                spent -= r["price"]
            if spent <= cash:
                break

    leftover = cash - spent

    if top_up:
        # One pass, largest weight first. Bounded by construction: every
        # purchase costs at least one share price, so `leftover` strictly
        # decreases and the loop cannot spin.
        progressed = True
        while progressed and leftover > 0:
            progressed = False
            for r in priced:
                if r["shares"] < r["max_shares"] and r["price"] <= leftover:
                    r["shares"] += 1
                    leftover -= r["price"]
                    spent += r["price"]
                    progressed = True

    allocations, unaffordable = [], []
    for r in priced:
        cost = round(r["shares"] * r["price"], 2)
        row = {**r, "amount": cost,
               "pct_of_cash": round(cost / cash * 100, 1) if cash else None}
        if r["shares"] < 1:
            row["reason"] = (
                f"₹{r['target_amount']:,.0f} was allocated but one share costs "
                f"₹{r['price']:,.2f} — not enough for a single share.")
            unaffordable.append(row)
        else:
            allocations.append(row)

    allocations.sort(key=lambda r: -r["amount"])
    return {
        "allocations": allocations,
        "unaffordable": unaffordable,
        "unpriced": unpriced,
        "totals": {
            "cash": round(cash, 2),
            "deployed": round(spent, 2),
            "leftover": round(max(cash - spent, 0), 2),
            "utilisation_pct": round(spent / cash * 100, 1) if cash else 0.0,
            "n_positions": len(allocations),
        },
        "note": ("Sized in whole shares at the live price, so every line is "
                 "placeable. The total stays under your budget — using every "
                 "last rupee isn't the goal."),
    }

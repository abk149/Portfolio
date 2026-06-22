"""Command handlers that wire the Telegram bot into the project's modules."""
from __future__ import annotations

import pandas as pd

from src.intraday import IntradayAnalyzer, IntradayScanner
from src.portfolio import PortfolioManager, PortfolioOptimizer, ReportBuilder
from src.screener import ScreenerEngine
from src.telegram.excel import (
    build_intraday_xlsx,
    build_optimizer_xlsx,
    build_screener_xlsx,
)


HELP = (
    "*Upstox Portfolio Bot*\n\n"
    "/upstox\\_login — log in to Upstox (refresh daily token)\n"
    "/upstox\\_status — check Upstox token\n"
    "/report — portfolio Excel (Holdings, Allocation, Positions)\n"
    "/screener [universe] — funnel scan + Excel (default `all_nse`)\n"
    "/optimize [max\\_sharpe|min\\_variance] — MPT optimizer + Excel\n"
    "/intraday [days] — analyze past trades + live scan, return Excel\n"
    "/agent <question> — ask the portfolio agent\n"
    "/help — this message"
)


def cmd_help(bot, chat_id, args):
    bot.send_message(chat_id, HELP)


# ---------- Upstox login over Telegram ----------
def cmd_upstox_login(bot, chat_id, args):
    """Two-step OAuth handshake. We send the auth URL; the user pastes the
    redirected URL (or just the `code` value) back to us in their next message."""
    from src.upstox.auth import build_auth_url

    if not _upstox_keys_set():
        bot.send_message(
            chat_id,
            "❌ Upstox API keys are not set. Add UPSTOX_API_KEY / UPSTOX_API_SECRET "
            "to `.env` and restart the bot.",
        )
        return None

    url = build_auth_url()
    bot.send_message(
        chat_id,
        "🔐 *Upstox login*\n\n"
        f"1. Open this link on any device:\n{url}\n\n"
        "2. Log in to Upstox.\n"
        "3. The browser will be redirected to a URL like\n"
        "   `http://localhost:8000/callback?code=XXXXXX` (page may fail to load — that's fine).\n"
        "4. *Copy that entire URL* from the address bar and paste it back here.\n"
        "   (Or just paste the value after `code=`.)\n\n"
        "I'll exchange it for your access token and save it.",
    )

    # Return the consumer for the NEXT message from this chat
    return _consume_upstox_code


def _consume_upstox_code(bot, chat_id, text):
    from src.upstox.auth import exchange_and_save
    from src.upstox.client import UpstoxClient

    code = _extract_code(text)
    if not code:
        bot.send_message(
            chat_id,
            "Couldn't find a `code=…` in that message. Send /upstox_login again and "
            "paste the full redirect URL.",
        )
        return

    try:
        exchange_and_save(code)
        prof = UpstoxClient().profile()
        name = prof.get("user_name") or prof.get("email") or "(unknown)"
        bot.send_message(chat_id, f"✅ Upstox authenticated as *{name}*. Token cached.")
    except Exception as e:
        bot.send_message(
            chat_id,
            f"❌ Token exchange failed: `{e}`\nThe code may have expired (they're "
            "one-shot, ~10 min). Run /upstox_login again.",
        )


def cmd_upstox_status(bot, chat_id, args):
    try:
        from src.upstox.client import UpstoxClient
        prof = UpstoxClient().profile()
        name = prof.get("user_name") or prof.get("email") or "(unknown)"
        bot.send_message(chat_id, f"✅ Upstox token OK · *{name}*")
    except Exception as e:
        bot.send_message(chat_id, f"❌ Upstox not authenticated: `{e}`\nUse /upstox_login.")


def _upstox_keys_set() -> bool:
    from config import settings
    return bool(settings.upstox_api_key and settings.upstox_api_secret)


def _extract_code(text: str) -> str | None:
    """Pull the `code` out of either a full redirect URL or a raw paste."""
    import re
    import urllib.parse

    text = text.strip().strip("`'\"")
    # Try as URL first
    try:
        q = urllib.parse.urlparse(text).query
        if q:
            params = urllib.parse.parse_qs(q)
            if "code" in params:
                return params["code"][0]
    except Exception:
        pass
    # Loose regex (handles "code=XYZ" or "code: XYZ")
    m = re.search(r"code[=:\s]+([A-Za-z0-9_\-\.]+)", text)
    if m:
        return m.group(1)
    # Bare token (no spaces, looks token-ish)
    if re.fullmatch(r"[A-Za-z0-9_\-\.]{8,}", text):
        return text
    return None


def cmd_report(bot, chat_id, args):
    bot.send_message(chat_id, "📊 Building portfolio report …")
    snap = PortfolioManager().snapshot()
    paths = ReportBuilder().build(snap)
    caption = (
        f"Invested ₹{snap.summary['holdings_invested']:.0f} | "
        f"Value ₹{snap.summary['holdings_value']:.0f} | "
        f"P&L ₹{snap.summary['holdings_pnl']:.0f} ({snap.summary['holdings_pnl_pct']:.2f}%)"
    )
    bot.send_document(chat_id, paths["xlsx"], caption=caption)


def cmd_screener(bot, chat_id, args):
    universe = args.strip() or "all_nse"
    bot.send_message(chat_id, f"🔎 Running funnel scan on `{universe}` … this can take a few minutes.")
    eng = ScreenerEngine()
    tech = eng.technical_scan(universe, tech_min=60.0)
    full = eng.fundamental_scan(tech, fund_min=50.0) if not tech.empty else pd.DataFrame()
    path = build_screener_xlsx(full, tech)
    n_buy = int((full.get("recommendation") == "STRONG_BUY").sum()) if not full.empty else 0
    bot.send_document(
        chat_id, path,
        caption=f"Technical: {len(tech)} • Combined: {len(full)} • STRONG_BUY: {n_buy}",
    )


def cmd_optimize(bot, chat_id, args):
    mode = (args.strip() or "max_sharpe").lower()
    bot.send_message(chat_id, f"📈 Optimizing portfolio ({mode}) …")
    pm, opt = PortfolioManager(), PortfolioOptimizer()
    snap = pm.snapshot()
    if snap.holdings.empty:
        bot.send_message(chat_id, "No holdings to optimize.")
        return
    tickers, val = [], {}
    for _, row in snap.holdings.iterrows():
        sym = row.get("tradingsymbol", "")
        yf = f"{sym}.NS"
        tickers.append((yf, row.get("instrument_token", "")))
        val[yf] = float(row.get("current_value", 0))
    rets = opt.returns(tickers)
    if rets.empty or rets.shape[1] < 2:
        bot.send_message(chat_id, "Insufficient overlapping history.")
        return
    res = opt.min_variance(rets) if mode == "min_variance" else opt.max_sharpe(rets)
    rebal = opt.rebalance_suggestion(val, res)
    frontier = opt.efficient_frontier(rets, points=15)
    path = build_optimizer_xlsx(res, rebal, frontier)
    bot.send_document(
        chat_id, path,
        caption=(
            f"{mode}  •  E[R] {res.expected_return*100:.1f}%  "
            f"•  Vol {res.volatility*100:.1f}%  •  Sharpe {res.sharpe:.2f}"
        ),
    )


def cmd_intraday(bot, chat_id, args):
    try:
        days = int(args.strip()) if args.strip() else 60
    except ValueError:
        days = 60
    bot.send_message(chat_id, f"⏱ Analyzing last {days} days + scanning live …")
    analysis = IntradayAnalyzer().analyze(days)
    scan = IntradayScanner().scan("nifty50", min_score=40)
    path = build_intraday_xlsx(analysis, scan)
    cap_bits = []
    if "win_rate_pct" in analysis:
        cap_bits.append(f"win-rate {analysis['win_rate_pct']}%")
    if "expectancy" in analysis:
        cap_bits.append(f"exp ₹{analysis['expectancy']}")
    cap_bits.append(f"{len(scan)} live setups")
    bot.send_document(chat_id, path, caption=" • ".join(cap_bits))


def cmd_agent(bot, chat_id, args):
    if not args.strip():
        bot.send_message(chat_id, "Usage: /agent <question>")
        return
    from src.agents import PortfolioAgent
    bot.send_message(chat_id, "🤖 Thinking …")
    answer = PortfolioAgent().run(args.strip())
    bot.send_message(chat_id, answer)


HANDLERS = {
    "start": cmd_help,
    "help": cmd_help,
    "upstox_login": cmd_upstox_login,
    "upstox_status": cmd_upstox_status,
    "report": cmd_report,
    "screener": cmd_screener,
    "optimize": cmd_optimize,
    "intraday": cmd_intraday,
    "agent": cmd_agent,
}

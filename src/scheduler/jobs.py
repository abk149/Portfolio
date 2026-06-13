"""APScheduler — runs the D-R1-Quant pipeline on Indian market hours (IST).

Jobs (all in Asia/Kolkata):
- 09:00  Pre-open macro check                 → broadcast Market Mode to Telegram
- 09:30  Full funnel (Stage 1 → 3)            → portfolio for the day
- 10:00, 12:00, 14:30  Intraday advisor       → fresh alerts
- 15:15  EOD summary                          → portfolio Excel via Telegram
"""
from __future__ import annotations

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.utils.logger import get_logger

log = get_logger("scheduler")
IST = pytz.timezone("Asia/Kolkata")


def _safe(fn):
    """Wrap a job so one failure doesn't kill the scheduler."""
    def _w():
        try:
            return fn()
        except Exception as e:
            log.exception(f"job {fn.__name__} failed: {e}")
    _w.__name__ = fn.__name__
    return _w


# ---------- jobs ----------
def job_macro_check():
    from src.tools import MacroSnapshot
    from src.db import get_db
    from src.telegram.bot import TelegramBot
    m = MacroSnapshot().market_mode()
    get_db().log_macro(m)
    try:
        TelegramBot().broadcast(
            f"📊 *Pre-open macro*  Mode: *{m['mode']}*\n"
            f"VIX {m.get('india_vix')}  •  PCR {m.get('nifty_pcr')}  •  "
            f"USDINR {m.get('usdinr')}\n" + "\n".join(f"• {r}" for r in m.get("reasons", []))
        )
    except Exception:
        pass


def job_full_funnel():
    job_full_funnel_sync()

def job_full_funnel_sync(universe: str = "nifty50"):
    from src.agents.quant_agent import DR1QuantAgent
    from src.telegram.bot import TelegramBot
    res = DR1QuantAgent(universe=universe).run()
    try:
        TelegramBot().broadcast(
            f"🧠 D-R1-Quant funnel done · {res['candidates']} candidates · "
            f"{len(res['validated'])} validated · Sharpe "
            f"{res.get('portfolio', {}).get('sharpe', '—')}"
        )
    except Exception:
        pass
    return res


def job_intraday():
    from src.intraday import IntradayScanner
    from src.telegram.bot import TelegramBot
    df = IntradayScanner().scan("nifty50", min_score=50)
    if df.empty:
        return
    text = "⚡ *Intraday setups*\n" + "\n".join(
        f"• {r.symbol} {r.direction}  entry {r.entry}  SL {r.stop}  T2 {r.target_2R}"
        for r in df.itertuples()
    )
    try:
        TelegramBot().broadcast(text)
    except Exception:
        pass


def job_eod_report():
    from src.portfolio import PortfolioManager, ReportBuilder
    from src.telegram.bot import TelegramBot
    snap = PortfolioManager().snapshot()
    paths = ReportBuilder().build(snap)
    try:
        TelegramBot().broadcast_document(
            paths["xlsx"],
            caption=f"📦 EOD report · P&L ₹{snap.summary['holdings_pnl']:.0f} "
                    f"({snap.summary['holdings_pnl_pct']:.2f}%)",
        )
    except Exception:
        pass


# ---------- assembly ----------
def build_scheduler(background: bool = False):
    Cls = BackgroundScheduler if background else BlockingScheduler
    sched = Cls(timezone=IST)
    sched.add_job(_safe(job_macro_check),   CronTrigger(hour=9,  minute=0,
                                                       day_of_week="mon-fri"))
    sched.add_job(_safe(job_full_funnel),   CronTrigger(hour=9,  minute=30,
                                                       day_of_week="mon-fri"))
    for h, mnt in [(10, 0), (12, 0), (14, 30)]:
        sched.add_job(_safe(job_intraday),  CronTrigger(hour=h,  minute=mnt,
                                                       day_of_week="mon-fri"))
    sched.add_job(_safe(job_eod_report),    CronTrigger(hour=15, minute=15,
                                                       day_of_week="mon-fri"))
    return sched


def run_blocking():
    sched = build_scheduler(background=False)
    log.info("Scheduler starting (IST). Ctrl-C to stop.")
    sched.start()

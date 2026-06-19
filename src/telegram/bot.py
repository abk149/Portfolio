"""Telegram bot, built on `python-telegram-bot` (async, httpx).

This replaces the raw-requests implementation — PTB handles TLS / connection
pooling / retries properly on macOS, fixing the ConnectionResetError(54) we
were seeing on broadcasts.

Public surface (unchanged so callers keep working):

  bot = TelegramBot()
  bot.send_message(chat_id, text)
  bot.send_document(chat_id, path, caption="…")
  bot.broadcast(text)
  bot.broadcast_document(path, caption="…")

  CommandBot(HANDLERS).run()         # blocking long-poll
  CommandBot(HANDLERS).start_thread()  # background thread

Authentication:
  - chat_ids in settings.telegram_allowed_chat_ids are always allowed.
  - Any other chat must do /auth <TELEGRAM_AUTH_SECRET> once; we then
    persist them to .cache/telegram_auth.json.

Handlers are sync (bot, chat_id, args) callables. We dispatch each one via
`asyncio.to_thread` so the event loop stays responsive. A handler may return
a callable to consume the next plain-text message from that chat (used for
the /upstox_login → paste-code flow).
"""
from __future__ import annotations

import asyncio
import inspect
import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

# NOTE: `python-telegram-bot` (the `telegram` package) is NOT imported at module
# level. It is only needed by the long-polling CommandBot, and it is not
# available on Android/Chaquopy (it pulls async httpx which doesn't build there).
# The send-only TelegramBot below uses plain `requests`, so broadcasts from the
# DR-Quant funnel etc. work everywhere. PTB is imported lazily inside CommandBot.
if TYPE_CHECKING:  # type hints only — never evaluated at runtime
    from telegram import Update
    from telegram.ext import Application, ContextTypes

from config import settings
from src.utils.logger import get_logger

log = get_logger("telegram")


def _import_ptb():
    """Lazily import python-telegram-bot. Raises a clear error if unavailable."""
    try:
        from telegram import Update
        from telegram.ext import (
            ApplicationBuilder, CommandHandler, ContextTypes,
            MessageHandler, filters,
        )
        return Update, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
    except ImportError as e:
        raise RuntimeError(
            "python-telegram-bot is not installed. The interactive Telegram "
            "command bot is unavailable on this platform (e.g. Android). "
            "Send-only broadcasts still work."
        ) from e


# ────────────────────── auth helpers ──────────────────────
def _load_authed() -> set[int]:
    p = settings.telegram_auth_file
    base = set(settings.telegram_allowed_chat_ids)
    if not p.exists():
        return base
    try:
        return base | set(json.loads(p.read_text()).get("chat_ids", []))
    except Exception:
        return base


def authorize_chat(chat_id: int) -> None:
    p = settings.telegram_auth_file
    p.parent.mkdir(parents=True, exist_ok=True)
    cur: set[int] = set()
    if p.exists():
        try:
            cur = set(json.loads(p.read_text()).get("chat_ids", []))
        except Exception:
            pass
    cur.add(int(chat_id))
    p.write_text(json.dumps({"chat_ids": sorted(cur)}, indent=2))
    log.info(f"Authorized chat_id {chat_id}")


def _is_authorized(chat_id: int) -> bool:
    return int(chat_id) in _load_authed()


def _accepts_dispatcher(fn) -> bool:
    try:
        return "dispatcher" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


# ────────────────────── send-only client ──────────────────────
# Uses plain `requests` (sync, no event loop). This is the safest API for code
# that lives inside FastAPI handlers — bridging PTB's async Bot back to sync
# inside a uvicorn worker is what caused the earlier ReadTimeouts. PTB itself
# is only used in the dedicated bot subprocess below (`CommandBot.run`).
class TelegramBot:
    API = "https://api.telegram.org"

    def __init__(self, token: Optional[str] = None):
        self.token = token or settings.telegram_bot_token
        if not self.token:
            raise RuntimeError(
                "Telegram token missing. Set TELE_TOKEN (or TELEGRAM_BOT_TOKEN) in .env."
            )

    @property
    def _base(self) -> str:
        return f"{self.API}/bot{self.token}"

    def _post(self, endpoint: str, data: dict, files=None, timeout: int = 30) -> dict:
        import requests
        # Disable keep-alive — macOS + Telegram + persistent connections
        # occasionally yield ConnectionResetError(54). One-shot conns are slower
        # but rock-solid.
        try:
            r = requests.post(
                f"{self._base}/{endpoint}",
                data=data, files=files, timeout=timeout,
                headers={"Connection": "close"},
            )
            return r.json()
        except Exception as e:
            return {"ok": False, "description": f"{type(e).__name__}: {e}"}

    def send_message(self, chat_id: int, text: str, parse_mode: str = "Markdown") -> dict:
        # Telegram caps at 4096 chars; chunk if needed
        last = {"ok": True}
        for i in range(0, len(text), 4000):
            chunk = text[i:i + 4000]
            last = self._post("sendMessage", {"chat_id": chat_id, "text": chunk,
                                              "parse_mode": parse_mode})
            if not last.get("ok") and "can't parse" in (last.get("description") or "").lower():
                # Markdown rejected — retry plain
                last = self._post("sendMessage", {"chat_id": chat_id, "text": chunk})
            if not last.get("ok"):
                log.warning(f"send_message → {chat_id} failed: {last.get('description')}")
                break
        return last

    def send_document(self, chat_id: int, path, caption: str = "") -> dict:
        path = str(path)
        with open(path, "rb") as fh:
            return self._post(
                "sendDocument",
                data={"chat_id": chat_id, "caption": caption},
                files={"document": (Path(path).name, fh)},
                timeout=120,
            )

    def get_me(self) -> dict:
        import requests
        try:
            return requests.get(f"{self._base}/getMe", timeout=15,
                                headers={"Connection": "close"}).json()
        except Exception as e:
            return {"ok": False, "description": str(e)}

    def broadcast(self, text: str) -> None:
        for cid in _load_authed():
            self.send_message(cid, text)

    def broadcast_document(self, path, caption: str = "") -> None:
        for cid in _load_authed():
            self.send_document(cid, path, caption)


# ────────────────────── long-polling command bot ──────────────────────
class CommandBot:
    """PTB-based command dispatcher. Wraps sync handlers (bot, chat_id, args)
    so the existing handlers.py works unchanged."""

    def __init__(self, handlers: dict[str, Callable], token: Optional[str] = None):
        self.token = token or settings.telegram_bot_token
        if not self.token:
            raise RuntimeError(
                "Telegram token missing. Set TELE_TOKEN in .env."
            )
        self.handlers = handlers
        self.app: Optional[Application] = None
        # chat_id → callable(bot, chat_id, text) consuming the NEXT plain-text msg
        self.pending: dict[int, Callable] = {}
        self.send_client = TelegramBot(self.token)
        self._thread: Optional[threading.Thread] = None

    # ---- adapters: sync handler → async PTB callback ----
    def _make_command_cb(self, name: str, handler: Callable):
        async def _cb(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
            msg = update.effective_message
            if msg is None:
                return
            chat_id = update.effective_chat.id

            if name == "auth":
                args = (msg.text or "").split(maxsplit=1)
                secret = args[1].strip() if len(args) > 1 else ""
                if settings.telegram_auth_secret and secret == settings.telegram_auth_secret:
                    authorize_chat(chat_id)
                    await msg.reply_text("✅ Authorized. Try /help.")
                else:
                    await msg.reply_text("❌ Invalid auth secret.")
                return

            if not _is_authorized(chat_id):
                await msg.reply_text(
                    f"Not authorized (chat_id={chat_id}). "
                    f"Use /auth <secret> or add this id to CHAT_ID in .env.",
                )
                return

            parts = (msg.text or "").split(maxsplit=1)
            args = parts[1] if len(parts) > 1 else ""

            def _run_sync():
                try:
                    if _accepts_dispatcher(handler):
                        return handler(self.send_client, chat_id, args, dispatcher=self)
                    return handler(self.send_client, chat_id, args)
                except Exception as e:
                    log.exception("handler error")
                    self.send_client.send_message(chat_id, f"⚠️ {e}")
                    return None

            result = await asyncio.to_thread(_run_sync)
            if callable(result):
                self.pending[chat_id] = result

        return _cb

    async def _text_cb(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
        msg = update.effective_message
        if not msg or not msg.text:
            return
        chat_id = update.effective_chat.id
        if not _is_authorized(chat_id):
            return
        cb = self.pending.pop(chat_id, None)
        if not cb:
            return
        def _run_sync():
            try:
                cb(self.send_client, chat_id, msg.text)
            except Exception as e:
                log.exception("pending handler error")
                self.send_client.send_message(chat_id, f"⚠️ {e}")
        await asyncio.to_thread(_run_sync)

    async def _error_cb(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        log.warning(f"Telegram error: {context.error}")

    # ---- lifecycle ----
    def _build(self) -> "Application":
        _Update, ApplicationBuilder, CommandHandler, _Ctx, MessageHandler, filters = _import_ptb()
        # Generous timeouts — macOS + IPv6 + corporate DNS can be slow on first
        # contact with api.telegram.org. PTB's defaults (5s) are too tight here.
        app = (
            ApplicationBuilder()
            .token(self.token)
            .connect_timeout(15.0)
            .read_timeout(30.0)
            .write_timeout(30.0)
            .pool_timeout(30.0)
            .get_updates_connect_timeout(15.0)
            .get_updates_read_timeout(45.0)
            .build()
        )
        # Always-available /auth handshake
        app.add_handler(CommandHandler("auth", self._make_command_cb("auth", lambda *a, **k: None)))
        for name, fn in self.handlers.items():
            app.add_handler(CommandHandler(name, self._make_command_cb(name, fn)))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._text_cb))
        app.add_error_handler(self._error_cb)
        return app

    def run(self):
        """Blocking long-poll. MUST be called from the main thread of its own
        process — PTB installs an asyncio loop here and calling it from a
        worker thread inside uvicorn causes httpx ReadTimeouts on getMe.

        The dashboard launches this via `subprocess.Popen([..., "telegram", "bot"])`
        which is exactly how Upstox_Agent does it.
        """
        Update = _import_ptb()[0]
        log.info(f"Telegram bot starting. Authorized: {sorted(_load_authed())}")
        self.app = self._build()
        self.app.run_polling(allowed_updates=Update.ALL_TYPES,
                             drop_pending_updates=True)

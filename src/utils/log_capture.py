"""Capture log records from named loggers into an in-memory list.

Used by the dashboard to stream a live "what's being analyzed now" feed for
long-running jobs (D-R1-Quant funnel, full-NSE screener). Each captured line
is `{"ts": iso, "level": "INFO", "logger": "...", "msg": "..."}`.

Usage:

    with LogCapture(["d-r1-quant", "screener", "tools", "screener.talib"]) as cap:
        DR1QuantAgent().run()
        cap.lines   # all records, growing in real time
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime
from typing import Iterable


class _BufferHandler(logging.Handler):
    def __init__(self, buf: deque, lock: threading.Lock):
        super().__init__()
        self.buf = buf
        self.lock = lock

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            msg = record.msg
        item = {
            "ts": datetime.utcfromtimestamp(record.created).isoformat(timespec="seconds") + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": msg,
        }
        with self.lock:
            self.buf.append(item)


class LogCapture:
    def __init__(self, loggers: Iterable[str], capacity: int = 5000):
        self.logger_names = list(loggers)
        self.buf: deque = deque(maxlen=capacity)
        self.lock = threading.Lock()
        self._handler = _BufferHandler(self.buf, self.lock)
        self._handler.setLevel(logging.DEBUG)
        self._attached: list[logging.Logger] = []

    @property
    def lines(self) -> list[dict]:
        with self.lock:
            return list(self.buf)

    def since(self, idx: int) -> tuple[list[dict], int]:
        with self.lock:
            data = list(self.buf)
        return data[idx:], len(data)

    def __enter__(self) -> "LogCapture":
        for name in self.logger_names:
            lg = logging.getLogger(name)
            # Make sure DEBUG/INFO records reach our handler regardless of
            # the logger's own level (some are WARNING by default).
            if lg.level == logging.NOTSET or lg.level > logging.INFO:
                lg.setLevel(logging.INFO)
            lg.addHandler(self._handler)
            self._attached.append(lg)
        return self

    def __exit__(self, *exc):
        for lg in self._attached:
            try:
                lg.removeHandler(self._handler)
            except Exception:
                pass
        self._attached.clear()
        return False

"""FallbackProvider — an ordered chain of LLM providers.

Tries each rung in turn for `complete()` and `tool_loop()`. A rung is treated
as FAILED when it raises or returns one of the providers' error sentinels
(e.g. "[nvidia error: …]", "[ollama error: …]"); the chain then moves to the
next rung. The final answer comes from the first rung that succeeds.

Used to make NVIDIA NIM the primary brain with local DeepSeek-R1 (Ollama) as
the safety net — and to chain several NVIDIA models/keys before the local one.
"""
from __future__ import annotations

from typing import Any, Callable

from src.utils.logger import get_logger
from .base import LLMProvider

log = get_logger("llm.chain")

# A return that starts with any of these is a hard provider failure → try next.
_ERROR_PREFIXES = ("[nvidia error", "[ollama error", "[llamacpp error",
                   "[anthropic error", "[llm error")


def _failed(text: str) -> bool:
    if not text or not text.strip():
        return True
    t = text.lstrip().lower()
    return any(t.startswith(p) for p in _ERROR_PREFIXES)


class FallbackProvider(LLMProvider):
    def __init__(self, providers: list[LLMProvider]):
        self.providers = [p for p in providers if p is not None]
        if not self.providers:
            raise RuntimeError("FallbackProvider needs at least one provider")
        self.name = "chain(" + " → ".join(p.name for p in self.providers) + ")"

    def complete(self, system: str, user: str) -> str:
        last = "[llm error: no providers]"
        for i, p in enumerate(self.providers):
            try:
                out = p.complete(system, user)
            except Exception as e:
                out = f"[llm error: {p.name}: {e}]"
            if not _failed(out):
                if i > 0:
                    log.info(f"LLM fallback: served by rung {i} ({p.name})")
                return out
            log.warning(f"LLM rung {i} ({p.name}) failed: {str(out)[:160]}")
            last = out
        return last

    def tool_loop(self, system: str, user: str, tools: list[dict],
                  dispatch: Callable[[str, dict], Any], max_turns: int = 8) -> str:
        last = "[llm error: no providers]"
        for i, p in enumerate(self.providers):
            try:
                out = p.tool_loop(system, user, tools, dispatch, max_turns)
            except Exception as e:
                out = f"[llm error: {p.name}: {e}]"
            if not _failed(out):
                if i > 0:
                    log.info(f"LLM fallback (tool_loop): served by rung {i} ({p.name})")
                return out
            log.warning(f"LLM rung {i} ({p.name}) tool_loop failed: {str(out)[:160]}")
            last = out
        return last

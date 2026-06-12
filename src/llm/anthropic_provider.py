"""Anthropic Claude provider — native tool use."""
from __future__ import annotations

import json
from typing import Any, Callable

from anthropic import Anthropic

from config import settings
from src.utils.logger import get_logger
from .base import LLMProvider

log = get_logger("llm.anthropic")


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self):
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set.")
        self.client = Anthropic(api_key=settings.anthropic_api_key)
        self.model = settings.anthropic_model

    def complete(self, system, user):
        resp = self.client.messages.create(
            model=self.model, max_tokens=2048, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(getattr(b, "text", "") for b in resp.content).strip()

    def tool_loop(self, system, user, tools, dispatch, max_turns=8):
        messages: list[dict] = [{"role": "user", "content": user}]
        for _ in range(max_turns):
            resp = self.client.messages.create(
                model=self.model, max_tokens=2048,
                system=system, tools=tools, messages=messages,
            )
            messages.append({"role": "assistant", "content": resp.content})
            tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            if resp.stop_reason != "tool_use" or not tool_uses:
                return "".join(getattr(b, "text", "") for b in resp.content).strip()
            results = []
            for tu in tool_uses:
                log.info(f"→ {tu.name}({json.dumps(tu.input)[:200]})")
                try:
                    out = dispatch(tu.name, tu.input)
                    content = json.dumps(out, default=str)[:12000]
                except Exception as e:
                    content = f"ERROR: {e}"
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": content})
            messages.append({"role": "user", "content": results})
        return "Max turns reached."

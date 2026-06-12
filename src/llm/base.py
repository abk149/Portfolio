"""Provider-agnostic interface for the agent loop.

A provider takes:
  - a system prompt
  - a user message
  - a list of tool descriptors (name, description, input_schema)
  - a `dispatch(name, kwargs) -> Any` callable that runs a tool

…and returns a final natural-language answer after orchestrating any number
of tool calls. The two concrete providers are Anthropic Claude (native tool
use) and Ollama (ReAct-style JSON loop).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def tool_loop(
        self,
        system: str,
        user: str,
        tools: list[dict],
        dispatch: Callable[[str, dict], Any],
        max_turns: int = 8,
    ) -> str:
        ...

    def complete(self, system: str, user: str) -> str:
        """Single-shot completion (no tool loop). Default implementation just
        runs tool_loop with no tools; providers can override for efficiency."""
        return self.tool_loop(system, user, tools=[],
                              dispatch=lambda *a, **k: None, max_turns=1)

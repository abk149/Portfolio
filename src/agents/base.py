"""Provider-agnostic agent base. Picks Claude or local Ollama (DeepSeek-R1)
from LLM_PROVIDER env var. Subclasses just declare SYSTEM, TOOLS, and
implement `_dispatch(name, kwargs)`.
"""
from __future__ import annotations

from typing import Any

from src.llm import get_llm
from src.utils.logger import get_logger

log = get_logger("agent")


class BaseAgent:
    SYSTEM: str = ""
    TOOLS: list[dict] = []
    MAX_TURNS = 12          # more headroom for KB lookups + tool calls

    def __init__(self):
        self.llm = get_llm()

    def _dispatch(self, name: str, kwargs: dict) -> Any:  # pragma: no cover
        raise NotImplementedError

    # ---- shared KB tool every agent inherits ----
    @property
    def KB_TOOL(self) -> dict:
        return {
            "name": "kb_search",
            "description": "Semantic search over the user's uploaded finance books, "
                           "journals, and research notes. Call this when the question "
                           "is conceptual (strategy, valuation theory, behavioural finance) "
                           "or when you need authoritative context to anchor an answer.",
            "input_schema": {"type": "object", "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 5},
            }, "required": ["query"]},
        }

    def _kb_dispatch(self, kwargs: dict) -> Any:
        try:
            from src.kb import KnowledgeBase
            return KnowledgeBase.get().search(kwargs["query"], k=kwargs.get("k", 5))
        except Exception as e:
            return {"error": f"kb unavailable: {e}"}

    def _kb_count(self) -> int:
        try:
            from src.kb import KnowledgeBase
            return KnowledgeBase.get().col.count()
        except Exception:
            return 0

    def run(self, user_message: str) -> str:
        kb_n = self._kb_count()
        sys_full = self.SYSTEM
        tools_full = list(self.TOOLS)
        if kb_n > 0:
            sys_full += (
                f"\n\nYou ALSO have `kb_search` available ({kb_n} indexed chunks "
                "from the user's finance library). Call it for conceptual / theory / "
                "strategy questions or whenever a textbook reference strengthens "
                "your answer. Cite the result's title in your reply."
            )
            tools_full.append(self.KB_TOOL)
        # else: don't expose kb_search at all — saves the model a wasted turn

        def _dispatch(name, kwargs):
            if name == "kb_search":
                return self._kb_dispatch(kwargs)
            return self._dispatch(name, kwargs)

        return self.llm.tool_loop(
            system=sys_full, user=user_message,
            tools=tools_full, dispatch=_dispatch,
            max_turns=self.MAX_TURNS,
        )

"""Ollama provider — ReAct-style JSON tool loop.

Designed for reasoning models like deepseek-r1:8b that don't reliably support
the OpenAI/Anthropic native tool-calling format. We instruct the model to
emit ONE JSON object per turn:

    {"tool": "<name>", "input": { ... }}        ← call a tool
    {"answer": "<final natural-language answer>"}  ← stop

The model's chain-of-thought inside <think>…</think> is stripped before
parsing. We tolerate extra prose around the JSON and pick the last valid
JSON block we can find.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

import requests

from config import settings
from src.utils.logger import get_logger
from .base import LLMProvider

log = get_logger("llm.ollama")

REACT_SYSTEM_SUFFIX = """

You have access to the following TOOLS. To use a tool, respond with EXACTLY one
JSON object and NOTHING ELSE outside the JSON, in this form:

  {"tool": "TOOL_NAME", "input": { ... arguments ... }}

When (and only when) you have gathered enough information to answer the user,
respond with EXACTLY one JSON object in this form:

  {"answer": "your final natural-language answer here"}

Rules:
- Never invent data. If you don't have it, call a tool.
- Each turn: think briefly, then output the JSON. No prose after the JSON.
- Tool arguments must match the schema. Omit arguments you don't need.

TOOLS:
{tools_json}
"""

_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _strip_think(text: str) -> str:
    return re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()


def _extract_json(text: str) -> dict | None:
    """Try to pull the LAST valid JSON object out of the model's response."""
    cleaned = _strip_think(text)
    # Try fenced ```json first
    fenced = re.findall(r"```(?:json)?\s*([\s\S]+?)```", cleaned)
    candidates = list(reversed(fenced)) + [cleaned]
    for cand in candidates:
        # try direct parse first
        try:
            return json.loads(cand.strip())
        except Exception:
            pass
        # last-resort: greedy brace match
        m = _JSON_RE.search(cand)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                continue
    return None


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self):
        self.host = settings.ollama_host.rstrip("/")
        self.model = settings.ollama_model
        self.options = {
            "temperature": settings.ollama_temperature,
            "num_ctx": settings.ollama_num_ctx,
        }

    # ---------- single-shot (no tool loop, no JSON protocol overhead) ----------
    def complete(self, system: str, user: str) -> str:
        msgs = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
        return self._chat(msgs)

    # ---------- low-level chat ----------
    def _chat(self, messages: list[dict]) -> str:
        r = requests.post(
            f"{self.host}/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": self.options,
            },
            timeout=600,
        )
        r.raise_for_status()
        return r.json()["message"]["content"]

    # ---------- ReAct loop ----------
    def tool_loop(self, system, user, tools, dispatch, max_turns=8):
        # Compact tool descriptors for the prompt
        tool_blob = json.dumps(
            [{"name": t["name"], "description": t["description"],
              "input_schema": t.get("input_schema", {})} for t in tools],
            indent=2,
        )
        full_system = system + REACT_SYSTEM_SUFFIX.replace("{tools_json}", tool_blob)
        messages: list[dict] = [
            {"role": "system", "content": full_system},
            {"role": "user", "content": user},
        ]

        for turn in range(max_turns):
            try:
                raw = self._chat(messages)
            except Exception as e:
                log.warning(f"[turn {turn}] ollama error: {e}")
                return f"[ollama error: {e}]"

            # Surface the model's chain-of-thought + answer into the debug pane.
            # We log the <think> block (truncated) and the visible answer
            # separately so users can audit reasoning without crashing the UI.
            thinks = re.findall(r"<think>([\s\S]*?)</think>", raw, flags=re.IGNORECASE)
            visible = _strip_think(raw)
            if thinks:
                think_text = "\n".join(thinks).strip()
                log.info(f"[turn {turn}] 💭 THINK:\n{think_text[:2000]}"
                         + ("…[truncated]" if len(think_text) > 2000 else ""))
            log.info(f"[turn {turn}] 📨 RESPONSE:\n{visible[:1500]}"
                     + ("…[truncated]" if len(visible) > 1500 else ""))

            messages.append({"role": "assistant", "content": raw})
            parsed = _extract_json(raw)

            if parsed is None:
                # Nudge the model back onto the protocol
                messages.append({
                    "role": "user",
                    "content": "Your last response was not valid JSON. Reply ONLY with a "
                               "single JSON object: either {\"tool\":...,\"input\":...} or "
                               "{\"answer\":...}.",
                })
                continue

            # Accept any JSON that looks like a final verdict OR uses our explicit
            # {"answer": ...} envelope. Saves a wasted turn per analysis.
            if "answer" in parsed:
                ans = parsed["answer"]
                return ans if isinstance(ans, str) else json.dumps(ans)
            if "verdict" in parsed or "recommendation" in parsed:
                return json.dumps(parsed)

            if "tool" in parsed:
                name = parsed["tool"]
                kwargs = parsed.get("input", {}) or {}
                log.info(f"→ {name}({json.dumps(kwargs)[:200]})")
                try:
                    out = dispatch(name, kwargs)
                    content = json.dumps(out, default=str)[:8000]
                except Exception as e:
                    content = f"ERROR: {e}"
                messages.append({
                    "role": "user",
                    "content": f"TOOL_RESULT[{name}]:\n{content}\n\n"
                               f"Reply with the next JSON ({{\"tool\":...}} to call "
                               f"another tool, or {{\"answer\":...}} when done).",
                })
                continue

            # JSON without tool/answer → ask for clarification
            messages.append({
                "role": "user",
                "content": "Your JSON must include either a 'tool' or an 'answer' key.",
            })

        return "Max agent turns reached without a final answer."

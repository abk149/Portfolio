"""llama.cpp (llama-server) provider — ReAct-style JSON tool loop.

Designed to interface with a local llama-server running in OpenAI compatibility mode.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

import requests

from config import settings
from src.utils.logger import get_logger
from .base import LLMProvider

log = get_logger("llm.llamacpp")

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
    cleaned = _strip_think(text)
    fenced = re.findall(r"```(?:json)?\s*([\s\S]+?)```", cleaned)
    candidates = list(reversed(fenced)) + [cleaned]
    for cand in candidates:
        try:
            return json.loads(cand.strip())
        except Exception:
            pass
        m = _JSON_RE.search(cand)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                continue
    return None


class LlamaCppProvider(LLMProvider):
    name = "llamacpp"

    def __init__(self):
        self.host = settings.ollama_host.rstrip("/")
        self.model = settings.ollama_model
        self.temperature = settings.ollama_temperature

    def complete(self, system: str, user: str) -> str:
        msgs = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
        return self._chat(msgs)

    def _chat(self, messages: list[dict]) -> str:
        r = requests.post(
            f"{self.host}/v1/chat/completions",
            json={
                "model": self.model,
                "messages": messages,
                "stream": False,
                "temperature": self.temperature,
            },
            timeout=600,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def tool_loop(self, system, user, tools, dispatch, max_turns=8):
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
                log.warning(f"[turn {turn}] llamacpp error: {e}")
                return f"[llamacpp error: {e}]"

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
                messages.append({
                    "role": "user",
                    "content": "Your last response was not valid JSON. Reply ONLY with a "
                               "single JSON object: either {\"tool\":...,\"input\":...} or "
                               "{\"answer\":...}.",
                })
                continue

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

            messages.append({
                "role": "user",
                "content": "Your JSON must include either a 'tool' or an 'answer' key.",
            })

        return "Max agent turns reached without a final answer."

"""NVIDIA NIM provider — hosted LLM over the internet (OpenAI-compatible REST).

Uses plain `requests` (no openai SDK) so it runs identically on the Mac AND
inside the phone's Chaquopy bundle (which pins pydantic 1.x and can't load the
openai SDK). Same ReAct JSON tool protocol as the Ollama/llamacpp providers,
so the funnel and agents work unchanged.

Config (.env — keep the key here, NEVER hardcode it in an APK):
    LLM_PROVIDER=nvidia
    NVIDIA_API_KEY=nvapi-...
    NVIDIA_MODEL=nvidia/nemotron-3-super-120b-a12b
    NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
"""
from __future__ import annotations

import json
import re

import requests

from config import settings
from src.utils.logger import get_logger
from .base import LLMProvider
from .ollama_provider import REACT_SYSTEM_SUFFIX, _extract_json, _strip_think

log = get_logger("llm.nvidia")


class NvidiaProvider(LLMProvider):
    name = "nvidia"

    def __init__(self):
        if not settings.nvidia_api_key:
            raise RuntimeError(
                "NVIDIA_API_KEY not set. Add it to .env (LLM_PROVIDER=nvidia)."
            )
        self.url = settings.nvidia_base_url.rstrip("/") + "/chat/completions"
        self.model = settings.nvidia_model
        self.temperature = settings.nvidia_temperature
        self.max_tokens = settings.nvidia_max_tokens
        self.headers = {
            "Authorization": f"Bearer {settings.nvidia_api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    # ---------- low-level chat ----------
    def _chat(self, messages: list[dict]) -> str:
        base_body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "top_p": 0.95,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        # Reasoning models (Nemotron) accept these; non-reasoning models 400 on
        # them — so we retry without the extras if the first call is rejected.
        attempts = [
            {**base_body,
             "chat_template_kwargs": {"enable_thinking": True},
             "reasoning_budget": self.max_tokens},
            base_body,
        ]
        last_err = ""
        for body in attempts:
            try:
                r = requests.post(self.url, headers=self.headers,
                                  json=body, timeout=240)
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                continue
            if r.status_code == 200:
                try:
                    msg = r.json()["choices"][0]["message"]
                except Exception as e:
                    return f"[nvidia parse error: {e}]"
                content = (msg.get("content") or "").strip()
                reasoning = (msg.get("reasoning_content") or "").strip()
                # Wrap reasoning in <think> so the existing strip/log path works
                return f"<think>{reasoning}</think>\n{content}" if reasoning else content
            last_err = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code in (400, 422):
                continue       # retry without reasoning extras
            break              # 401/403/429/5xx — don't bother retrying extras
        log.warning(f"nvidia request failed: {last_err}")
        return f"[nvidia error: {last_err}]"

    # ---------- single-shot ----------
    def complete(self, system: str, user: str) -> str:
        return _strip_think(self._chat([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]))

    # ---------- ReAct tool loop ----------
    def tool_loop(self, system, user, tools, dispatch, max_turns=8):
        tool_blob = json.dumps(
            [{"name": t["name"], "description": t["description"],
              "input_schema": t.get("input_schema", {})} for t in tools],
            indent=2,
        )
        full_system = system + REACT_SYSTEM_SUFFIX.replace("{tools_json}", tool_blob)
        messages = [
            {"role": "system", "content": full_system},
            {"role": "user", "content": user},
        ]

        for turn in range(max_turns):
            raw = self._chat(messages)
            thinks = re.findall(r"<think>([\s\S]*?)</think>", raw, flags=re.IGNORECASE)
            visible = _strip_think(raw)
            if thinks:
                log.info(f"[turn {turn}] 💭 THINK:\n{chr(10).join(thinks)[:2000]}")
            log.info(f"[turn {turn}] 📨 RESPONSE:\n{visible[:1500]}")

            messages.append({"role": "assistant", "content": visible or raw})
            parsed = _extract_json(raw)

            if parsed is None:
                messages.append({"role": "user", "content":
                    "Reply ONLY with one JSON object: "
                    "{\"tool\":...,\"input\":...} or {\"answer\":...}."})
                continue
            if "answer" in parsed:
                a = parsed["answer"]
                return a if isinstance(a, str) else json.dumps(a)
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
                messages.append({"role": "user", "content":
                    f"TOOL_RESULT[{name}]:\n{content}\n\n"
                    f"Next JSON ({{\"tool\":...}} or {{\"answer\":...}})."})
                continue
            messages.append({"role": "user", "content":
                "Your JSON must include either a 'tool' or an 'answer' key."})

        return "Max agent turns reached without a final answer."

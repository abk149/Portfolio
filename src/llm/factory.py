from __future__ import annotations

import os

from config import settings
from src.utils.logger import get_logger
from .base import LLMProvider

log = get_logger("llm.factory")


def _single(name: str) -> LLMProvider:
    if name == "ollama":
        from .ollama_provider import OllamaProvider
        return OllamaProvider()
    if name == "llamacpp":
        from .llamacpp_provider import LlamaCppProvider
        return LlamaCppProvider()
    if name == "nvidia":
        from .nvidia_provider import NvidiaProvider
        return NvidiaProvider()
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider()
    raise ValueError(
        f"Unknown LLM provider '{name}'. Use 'nvidia', 'ollama', 'llamacpp', or 'anthropic'.")


def get_llm() -> LLMProvider:
    """Active LLM. For provider=nvidia we build a FALLBACK CHAIN:
        NVIDIA model(s)  →  local DeepSeek-R1 (Ollama)
    so NVIDIA is the main brain and the local model is the safety net. Add more
    NVIDIA models via NVIDIA_MODELS / NVIDIA_MODELS_JSON (see settings).
    Other providers stay single (unchanged behaviour)."""
    p = (settings.llm_provider or "anthropic").lower()

    if p != "nvidia":
        return _single(p)

    # ---- NVIDIA primary with chain + local fallback ----
    from .nvidia_provider import NvidiaProvider
    rungs: list[LLMProvider] = []
    for r in settings.nvidia_chain():
        try:
            rungs.append(NvidiaProvider(model=r["model"], api_key=r["api_key"],
                                        base_url=r["base_url"]))
        except Exception as e:
            log.warning(f"skip NVIDIA model {r.get('model')}: {e}")

    # Local DeepSeek-R1 fallback (skip on the phone — no local Ollama there)
    on_android = os.getenv("APP_FILES_DIR") is not None
    fb = settings.llm_fallback
    if fb != "none" and not on_android:
        try:
            rungs.append(_single(fb))
        except Exception as e:
            log.debug(f"local fallback '{fb}' unavailable: {e}")

    if not rungs:
        raise RuntimeError(
            "No usable LLM: set NVIDIA_API_KEY (and/or run Ollama for fallback).")
    if len(rungs) == 1:
        return rungs[0]
    from .fallback_provider import FallbackProvider
    chain = FallbackProvider(rungs)
    log.info(f"LLM chain: {chain.name}")
    return chain


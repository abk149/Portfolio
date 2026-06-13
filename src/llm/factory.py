from __future__ import annotations

from config import settings
from .base import LLMProvider


def get_llm() -> LLMProvider:
    p = (settings.llm_provider or "anthropic").lower()
    if p == "ollama":
        from .ollama_provider import OllamaProvider
        return OllamaProvider()
    if p == "llamacpp":
        from .llamacpp_provider import LlamaCppProvider
        return LlamaCppProvider()
    if p == "nvidia":
        from .nvidia_provider import NvidiaProvider
        return NvidiaProvider()
    if p == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider()
    raise ValueError(
        f"Unknown LLM_PROVIDER={p}. Use 'nvidia', 'ollama', 'llamacpp', or 'anthropic'.")


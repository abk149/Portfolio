"""Central config loader. All modules import settings from here."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    upstox_api_key: str = os.getenv("UPSTOX_API_KEY", "")
    upstox_api_secret: str = os.getenv("UPSTOX_API_SECRET", "")
    upstox_redirect_uri: str = os.getenv(
        "UPSTOX_REDIRECT_URI", "http://localhost:8765/callback"
    )
    upstox_token_file: Path = ROOT / os.getenv(
        "UPSTOX_TOKEN_FILE", ".cache/upstox_token.json"
    )

    # Which LLM brain to use for the agent layer: "anthropic" or "ollama"
    llm_provider: str = os.getenv("LLM_PROVIDER", "anthropic")

    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-7")

    # Local Ollama (e.g. DeepSeek-R1-Distill-Llama-8B) — runs on your M4
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "deepseek-r1:8b")
    ollama_temperature: float = float(os.getenv("OLLAMA_TEMPERATURE", "0.2"))
    ollama_num_ctx: int = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
    # Embedding model for the Knowledge Base's semantic search.
    # `ollama pull nomic-embed-text` to enable it; otherwise KB uses FTS5.
    ollama_embed_model: str = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    # Retrieved chunks below this cosine similarity are dropped — keeps
    # irrelevant context out of the LLM prompt.
    kb_min_similarity: float = float(os.getenv("KB_MIN_SIMILARITY", "0.35"))

    cache_dir: Path = ROOT / os.getenv("CACHE_DIR", ".cache")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    upstox_base_url: str = "https://api.upstox.com/v2"

    # Match the user's existing Upstox_Agent env var names (TELE_TOKEN / CHAT_ID),
    # with fallback to the longer original names so old configs keep working.
    telegram_bot_token: str = os.getenv("TELE_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_allowed_chat_ids: tuple = tuple(
        int(x) for x in (
            os.getenv("CHAT_ID") or os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
        ).replace(" ", "").split(",") if x
    )
    telegram_auth_secret: str = os.getenv("TELEGRAM_AUTH_SECRET", "")
    telegram_auth_file: Path = ROOT / ".cache" / "telegram_auth.json"

    # D-R1-Quant
    timescale_dsn: str = os.getenv("TIMESCALE_DSN", "")
    chroma_dir: Path = ROOT / os.getenv("CHROMA_DIR", ".cache/chroma")
    trace_dir: Path = ROOT / os.getenv("TRACE_DIR", ".cache/traces")
    risk_free_rate: float = float(os.getenv("RISK_FREE_RATE", "0.07"))


settings = Settings()
settings.cache_dir.mkdir(parents=True, exist_ok=True)
settings.upstox_token_file.parent.mkdir(parents=True, exist_ok=True)

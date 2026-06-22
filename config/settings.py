"""Central config loader. All modules import settings from here."""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

APP_FILES_DIR = os.getenv("APP_FILES_DIR")
if APP_FILES_DIR:
    ROOT = Path(APP_FILES_DIR)
    load_dotenv(override=False)
else:
    ROOT = Path(__file__).resolve().parent.parent
    load_dotenv()

class Settings:
    """Live Settings. Fetches from os.environ on every access to ensure
    Android-injected keys are always fresh.
    """

    def refresh(self):
        """No-op for property-based settings, kept for backward compatibility."""
        pass

    def _get(self, key: str, default: str = "") -> str:
        # A set-but-empty env var (e.g. UPSTOX_BASE_URL="") must NOT override a
        # sane default — otherwise it can produce relative/dead URLs. Treat
        # blank/whitespace as "unset → use default".
        val = os.getenv(key)
        if val is not None and val.strip():
            return val.strip()
        return default

    # ── Active broker (either/or) ──
    @property
    def broker(self) -> str:
        # env BROKER wins; else a persisted .cache/active_broker.txt; else upstox
        b = os.getenv("BROKER")
        if not b:
            try:
                f = ROOT / ".cache" / "active_broker.txt"
                if f.exists():
                    b = f.read_text().strip()
            except Exception:
                b = None
        b = (b or "upstox").lower()
        return b if b in ("upstox", "groww") else "upstox"

    # ── Groww ──
    @property
    def groww_api_key(self) -> str: return self._get("GROWW_API_KEY")

    @property
    def groww_api_secret(self) -> str: return self._get("GROWW_API_SECRET")

    @property
    def groww_access_token(self) -> str: return self._get("GROWW_ACCESS_TOKEN")

    @property
    def groww_token_file(self) -> Path:
        p = os.getenv("GROWW_TOKEN_FILE")
        return ROOT / p if p else ROOT / ".cache/groww_token.json"

    @property
    def upstox_api_key(self) -> str: return self._get("UPSTOX_API_KEY")

    @property
    def upstox_api_secret(self) -> str: return self._get("UPSTOX_API_SECRET")

    @property
    def upstox_redirect_uri(self) -> str:
        return self._get("UPSTOX_REDIRECT_URI", "http://127.0.0.1:8000/callback")

    @property
    def upstox_token_file(self) -> Path:
        p = os.getenv("UPSTOX_TOKEN_FILE")
        return ROOT / p if p else ROOT / ".cache/upstox_token.json"

    @property
    def llm_provider(self) -> str: return self._get("LLM_PROVIDER", "anthropic")

    @property
    def anthropic_api_key(self) -> str: return self._get("ANTHROPIC_API_KEY")

    @property
    def anthropic_model(self) -> str: return self._get("ANTHROPIC_MODEL", "claude-opus-4-7")

    @property
    def nvidia_api_key(self) -> str: return self._get("NVIDIA_API_KEY")

    @property
    def nvidia_base_url(self) -> str:
        return self._get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")

    @property
    def nvidia_model(self) -> str:
        return self._get("NVIDIA_MODEL", "nvidia/nemotron-3-super-120b-a12b")

    @property
    def nvidia_temperature(self) -> float:
        return float(self._get("NVIDIA_TEMPERATURE", "0.6"))

    @property
    def nvidia_max_tokens(self) -> int:
        return int(self._get("NVIDIA_MAX_TOKENS", "16384"))

    @property
    def nvidia_embed_model(self) -> str:
        return self._get("NVIDIA_EMBED_MODEL", "nvidia/nv-embedqa-e5-v5")

    def nvidia_chain(self) -> list:
        """Ordered list of NVIDIA rungs: [{"model","api_key","base_url"}, …].

        Two ways to register more NVIDIA models (try the first that answers):
          • NVIDIA_MODELS_JSON  — JSON list, e.g.
              [{"model":"meta/llama-3.1-405b-instruct","api_key":"nvapi-..."},
               {"model":"qwen/qwen2.5-72b-instruct"}]
            (api_key/base_url optional per entry → fall back to the defaults)
          • NVIDIA_MODELS  — simple comma-separated extra model names that all
            use NVIDIA_API_KEY, appended after NVIDIA_MODEL.
        The primary NVIDIA_MODEL is always rung 0.
        """
        import json
        default_key = self.nvidia_api_key
        default_url = self.nvidia_base_url
        rungs: list = []
        seen: set = set()

        def _add(model, key=None, url=None):
            model = (model or "").strip()
            if not model or model in seen:
                return
            seen.add(model)
            rungs.append({"model": model,
                          "api_key": (key or default_key),
                          "base_url": (url or default_url)})

        _add(self.nvidia_model)                       # primary
        raw_json = self._get("NVIDIA_MODELS_JSON")
        if raw_json:
            try:
                for e in json.loads(raw_json):
                    if isinstance(e, dict):
                        _add(e.get("model"), e.get("api_key"), e.get("base_url"))
                    elif isinstance(e, str):
                        _add(e)
            except Exception:
                pass
        for m in self._get("NVIDIA_MODELS").split(","):
            _add(m)
        # keep only rungs that actually have a key
        return [r for r in rungs if r["api_key"]]

    @property
    def llm_fallback(self) -> str:
        """Local fallback brain when NVIDIA rungs all fail. "ollama" (DeepSeek-R1)
        by default; "none" to disable."""
        return self._get("LLM_FALLBACK", "ollama").lower()

    @property
    def ollama_host(self) -> str:
        return self._get("OLLAMA_HOST", "http://127.0.0.1:11434")

    @property
    def ollama_model(self) -> str:
        return self._get("OLLAMA_MODEL", "deepseek-r1:8b")

    @property
    def ollama_temperature(self) -> float:
        return float(self._get("OLLAMA_TEMPERATURE", "0.2"))

    @property
    def ollama_num_ctx(self) -> int:
        return int(self._get("OLLAMA_NUM_CTX", "8192"))

    @property
    def ollama_embed_model(self) -> str:
        return self._get("OLLAMA_EMBED_MODEL", "nomic-embed-text")

    @property
    def kb_min_similarity(self) -> float:
        return float(self._get("KB_MIN_SIMILARITY", "0.35"))

    @property
    def cache_dir(self) -> Path:
        p = os.getenv("CACHE_DIR")
        return ROOT / p if p else ROOT / ".cache"

    @property
    def log_level(self) -> str: return self._get("LOG_LEVEL", "INFO")

    @property
    def upstox_base_url(self) -> str:
        return self._get("UPSTOX_BASE_URL", "https://api.upstox.com/v2")

    @property
    def telegram_bot_token(self) -> str:
        return os.getenv("TELE_TOKEN") or self._get("TELEGRAM_BOT_TOKEN")

    @property
    def telegram_allowed_chat_ids(self) -> tuple:
        s = os.getenv("CHAT_ID") or self._get("TELEGRAM_ALLOWED_CHAT_IDS")
        if not s: return ()
        return tuple(int(x) for x in s.replace(" ", "").split(",") if x)

    @property
    def telegram_auth_secret(self) -> str: return self._get("TELEGRAM_AUTH_SECRET")

    @property
    def telegram_auth_file(self) -> Path:
        return self.cache_dir / "telegram_auth.json"

    @property
    def timescale_dsn(self) -> str: return self._get("TIMESCALE_DSN")

    @property
    def chroma_dir(self) -> Path:
        p = os.getenv("CHROMA_DIR")
        return ROOT / p if p else self.cache_dir / "chroma"

    @property
    def trace_dir(self) -> Path:
        p = os.getenv("TRACE_DIR")
        return ROOT / p if p else self.cache_dir / "traces"

    @property
    def risk_free_rate(self) -> float:
        return float(self._get("RISK_FREE_RATE", "0.07"))

settings = Settings()
# Ensure directories exist
os.makedirs(settings.cache_dir, exist_ok=True)
os.makedirs(settings.chroma_dir, exist_ok=True)
os.makedirs(settings.upstox_token_file.parent, exist_ok=True)

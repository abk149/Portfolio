"""Android background runner helpers for Chaquopy."""
from __future__ import annotations

import os
import sys
from typing import Any

def start_dashboard(app_files_dir: str, receiver: Any) -> None:
    # Configure environment variables before importing anything else
    os.environ["APP_FILES_DIR"] = app_files_dir

    # ── LLM Configuration ──────────────────────────────────────────────────
    # The LLM_PROVIDER and API keys are injected by the Kotlin layer
    # (PortfolioService.startServers) into os.environ before calling this.
    # We only set a default if they are missing.
    if not os.environ.get("LLM_PROVIDER"):
        os.environ["LLM_PROVIDER"] = "nvidia"

    # Make sure the bundled .env is loaded so NVIDIA_API_KEY / UPSTOX_* resolve,
    # regardless of Chaquopy's working directory.
    try:
        from dotenv import load_dotenv
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        if os.path.exists(env_path):
            load_dotenv(env_path, override=False)
    except Exception as e:
        print(f"[Android Runner] .env load skipped: {e}")

    if not os.environ.get("NVIDIA_API_KEY"):
        print("[Android Runner] WARNING: NVIDIA_API_KEY is not set — analysis "
              "(LLM) calls will fail. Put it in the project .env before building.")

    # Set up UI mode to mobile
    try:
        # Create directories and write uiMode
        cache_dir = os.path.join(app_files_dir, ".cache")
        os.makedirs(cache_dir, exist_ok=True)
        with open(os.path.join(cache_dir, "ui_mode.txt"), "w") as f:
            f.write("mobile")
    except Exception as e:
        print(f"[Android Runner] failed to write ui_mode.txt: {e}")

    # Redirect stdout and stderr to Kotlin receiver
    class KotlinWriter:
        def __init__(self, rx):
            self.rx = rx
        def write(self, text):
            if text and text.strip():
                # Call Java/Kotlin method
                self.rx.log(text.strip())
        def flush(self):
            pass

    writer = KotlinWriter(receiver)
    sys.stdout = writer
    sys.stderr = writer

    print("[Android Runner] Starting FastAPI uvicorn server on port 8000...")
    
    import uvicorn
    # Import the FastAPI app
    from src.dashboard.app import app
    from config import settings

    # Force settings to reload environment variables injected by Kotlin
    print("[Android Runner] Refreshing settings from os.environ...")
    settings.refresh()

    if settings.upstox_api_key:
        print(f"[Android Runner] UPSTOX_API_KEY loaded: {settings.upstox_api_key[:4]}***")
    else:
        print("[Android Runner] ERROR: UPSTOX_API_KEY is still empty after refresh!")

    # Use a minimal log config to avoid "formatter 'default'" errors on Android
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "simple": {
                "format": "%(levelname)s: %(message)s"
            }
        },
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "formatter": "simple",
                "stream": "ext://sys.stdout"
            }
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["stdout"],
                "level": "INFO"
            },
            "uvicorn.error": {
                "level": "INFO"
            },
            "uvicorn.access": {
                "handlers": ["stdout"],
                "level": "INFO",
                "propagate": False
            }
        }
    }

    # Bind IPv4 loopback. The app's HTTP client AND the on-device browser both
    # reach the backend via 127.0.0.1, and the Upstox redirect is registered as
    # http://127.0.0.1:8000/callback — so everything is unambiguous IPv4.
    # (Do NOT bind "::" here: on Android uvicorn's IPv6 socket can end up
    # IPv6-only, which makes 127.0.0.1 unreachable — "backend unreachable".)
    print("[Android Runner] Binding 127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False, log_config=log_config)

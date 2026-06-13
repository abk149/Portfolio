"""Android background runner helpers for Chaquopy."""
from __future__ import annotations

import os
import sys
from typing import Any

def start_dashboard(app_files_dir: str, receiver: Any) -> None:
    # Configure environment variables before importing anything else
    os.environ["APP_FILES_DIR"] = app_files_dir

    # ── LLM: NVIDIA NIM over the internet (no on-device model) ───────────────
    # The analysis brain runs in NVIDIA's cloud. The NVIDIA_API_KEY comes from
    # the bundled .env (copied from the Mac project at build time) OR from an
    # env var the Kotlin layer set before calling us. We do NOT hardcode it.
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
    
    # Run uvicorn server
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False, log_level="info")

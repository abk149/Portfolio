"""Local embeddings via Ollama — semantic search with zero fragile deps.

We already run Ollama for the LLM, so we reuse it for embeddings too:
  POST {OLLAMA_HOST}/api/embeddings  {"model": "...", "prompt": "..."}

Vectors are stored as float32 blobs in the same SQLite KB; similarity is a
plain numpy cosine. No ChromaDB, no Rust, no native vector extension.

Default model: `nomic-embed-text` (768-dim, ~274 MB). Pull it once:
    ollama pull nomic-embed-text
If the model isn't present the KB silently degrades to FTS5 keyword search.
"""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np
import requests

from config import settings
from src.utils.logger import get_logger

log = get_logger("kb.embed")

_AVAILABLE: Optional[bool] = None      # tri-state: None=unknown, True/False=checked
_LOCK = threading.Lock()


def _model() -> str:
    return getattr(settings, "ollama_embed_model", None) or "nomic-embed-text"


def is_available() -> bool:
    """Check (once) whether the LLM server can serve embeddings."""
    global _AVAILABLE
    if _AVAILABLE is not None:
        return _AVAILABLE
    with _LOCK:
        if _AVAILABLE is not None:
            return _AVAILABLE
        try:
            if settings.llm_provider == "llamacpp":
                r = requests.post(
                    f"{settings.ollama_host}/embedding",
                    json={"content": "ping"},
                    timeout=20,
                )
                ok = r.status_code == 200 and isinstance(
                    r.json().get("embedding"), list)
                if ok:
                    log.info(f"embeddings: LlamaCpp server ready ({len(r.json()['embedding'])}-dim)")
                _AVAILABLE = ok
            else:
                r = requests.post(
                    f"{settings.ollama_host}/api/embeddings",
                    json={"model": _model(), "prompt": "ping"},
                    timeout=20,
                )
                ok = r.status_code == 200 and isinstance(
                    r.json().get("embedding"), list)
                if ok:
                    log.info(f"embeddings: Ollama model '{_model()}' ready "
                             f"({len(r.json()['embedding'])}-dim)")
                else:
                    log.warning(
                        f"embeddings: Ollama returned {r.status_code} for "
                        f"'{_model()}'. KB will use keyword search only. "
                        f"Enable semantic search with:  ollama pull {_model()}")
                _AVAILABLE = ok
        except Exception as e:
            log.warning(f"embeddings: LLM server unreachable ({e}) — "
                        f"KB falls back to keyword search")
            _AVAILABLE = False
    return _AVAILABLE


def embed(text: str) -> Optional[np.ndarray]:
    """Embed one piece of text → float32 vector, or None on failure."""
    if not text or not text.strip() or not is_available():
        return None
    try:
        if settings.llm_provider == "llamacpp":
            r = requests.post(
                f"{settings.ollama_host}/embedding",
                json={"content": text[:8000]},
                timeout=60,
            )
        else:
            r = requests.post(
                f"{settings.ollama_host}/api/embeddings",
                json={"model": _model(), "prompt": text[:8000]},
                timeout=60,
            )
        vec = r.json().get("embedding")
        if vec:
            return np.asarray(vec, dtype=np.float32)
    except Exception as e:
        log.debug(f"embed failed: {e}")
    return None


# ---------- blob (de)serialisation for SQLite storage ----------
def to_blob(vec: Optional[np.ndarray]) -> Optional[bytes]:
    return None if vec is None else np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: Optional[bytes]) -> Optional[np.ndarray]:
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32)


# ---------- similarity ----------
def cosine_rank(query_vec: np.ndarray,
                rows: list[tuple]) -> list[tuple]:
    """rows: list of (payload, blob). Returns [(payload, similarity)] sorted
    descending by cosine similarity. Rows without an embedding are dropped."""
    mats, payloads = [], []
    for payload, blob in rows:
        v = from_blob(blob)
        if v is not None and v.shape == query_vec.shape:
            mats.append(v)
            payloads.append(payload)
    if not mats:
        return []
    M = np.vstack(mats)
    qn = query_vec / (np.linalg.norm(query_vec) + 1e-9)
    Mn = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
    sims = Mn @ qn
    order = np.argsort(-sims)
    return [(payloads[i], float(sims[i])) for i in order]

"""VectorStore — now a thin shim over the SQLite KnowledgeBase.

Kept as a separate class only so existing call-sites (WebSearcher,
DR1QuantAgent) don't have to change. News snippets are stored as KB chunks
under a synthetic doc per search query, and `search()` delegates to the KB's
FTS5 full-text search.

(The old ChromaDB-backed implementation was removed — its Rust core caused
repeated install/version breakage.)
"""
from __future__ import annotations

import hashlib
from typing import Optional

from src.utils.logger import get_logger

log = get_logger("vector")


class VectorStore:
    def __init__(self, collection: str = "news"):
        self.collection = collection
        try:
            from src.kb import KnowledgeBase
            self._kb = KnowledgeBase.get()
        except Exception as e:
            log.debug(f"VectorStore: KB unavailable ({e}) — no-op mode")
            self._kb = None

    def add_news(self, query: str, results: list[dict]) -> None:
        if not self._kb or not results:
            return
        chunks = []
        for r in results:
            doc = f"{r.get('title','')}\n{r.get('snippet','')}".strip()
            if doc:
                chunks.append(doc)
        if not chunks:
            return
        doc_id = "news_" + hashlib.sha1(query.encode()).hexdigest()[:12]
        try:
            self._kb.add_chunks(doc_id, source=f"news:{query}",
                                title=f"News — {query}", chunks=chunks,
                                extra_meta={"kind": "news", "query": query})
        except Exception as e:
            log.debug(f"VectorStore.add_news failed: {e}")

    def search(self, query: str, k: int = 5) -> list[dict]:
        if not self._kb:
            return []
        try:
            return self._kb.search(query, k=k)
        except Exception:
            return []

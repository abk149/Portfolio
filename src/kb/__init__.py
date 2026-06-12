"""Knowledge Base — your private corpus of finance books / journals / notes.

Uploaded documents are:
  1. Text-extracted (PDF, EPUB, txt, markdown all supported)
  2. Chunked to ~500-token windows with 50-token overlap
  3. Embedded by ChromaDB's default sentence-transformer (cached locally)
  4. Stored in `.cache/chroma/kb/` for retrieval by the Ask-AI agents
  5. Re-emittable as JSONL for MLX-LM fine-tuning of DeepSeek-R1

The vector index lives in ChromaDB; sentence-transformer embeddings make the
on-disk footprint roughly 1/4 the original document size (we store text +
384-dim float vectors).
"""
from .store import KnowledgeBase  # noqa: F401
from .ingest import ingest_file, ingest_text  # noqa: F401

"""Document → text → chunks → vector store.

Supported formats: .pdf, .epub, .txt, .md, .markdown

Chunking strategy: ~500 word windows, 50 word overlap. Words ≈ tokens within
a small factor for English finance text. Cheap, format-agnostic, and good
enough for retrieval-augmented Q&A.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

from config import settings
from src.kb.store import KnowledgeBase
from src.utils.logger import get_logger

log = get_logger("kb.ingest")


CHUNK_WORDS = 500
OVERLAP_WORDS = 50


# ---------- format-specific readers ----------
def _read_pdf(p: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(p))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _read_epub(p: Path) -> str:
    try:
        from ebooklib import epub, ITEM_DOCUMENT
        from bs4 import BeautifulSoup
    except ImportError:
        raise RuntimeError("EPUB support needs `pip install ebooklib beautifulsoup4`")
    book = epub.read_epub(str(p))
    out: list[str] = []
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        out.append(BeautifulSoup(item.get_content(), "html.parser").get_text(" ",
                                                                              strip=True))
    return "\n".join(out)


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


# ---------- chunking ----------
def _chunk(text: str, size: int = CHUNK_WORDS,
           overlap: int = OVERLAP_WORDS) -> list[str]:
    # Normalise whitespace, then slide a word window
    text = re.sub(r"\s+", " ", text).strip()
    words = text.split(" ")
    if len(words) <= size:
        return [text] if text else []
    chunks = []
    step = size - overlap
    for i in range(0, len(words), step):
        chunk = " ".join(words[i:i + size])
        if chunk:
            chunks.append(chunk)
        if i + size >= len(words):
            break
    return chunks


# ---------- public API ----------
def ingest_text(title: str, text: str, source: str = "manual",
                extra_meta: Optional[dict] = None) -> dict:
    doc_id = hashlib.sha1(f"{title}|{text[:1000]}".encode("utf-8")).hexdigest()[:16]
    chunks = _chunk(text)
    if not chunks:
        return {"ok": False, "error": "empty document"}
    n = KnowledgeBase.get().add_chunks(doc_id, source, title, chunks, extra_meta)
    return {"ok": True, "doc_id": doc_id, "title": title,
            "chunks_indexed": n, "chars": len(text)}


def ingest_file(path: str | Path, title: Optional[str] = None) -> dict:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"file not found: {p}"}

    ext = p.suffix.lower()
    title = title or p.stem.replace("_", " ")

    try:
        if ext == ".pdf":
            text = _read_pdf(p)
        elif ext == ".epub":
            text = _read_epub(p)
        elif ext in (".txt", ".md", ".markdown"):
            text = _read_text(p)
        else:
            return {"ok": False, "error": f"unsupported format: {ext}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    if not text.strip():
        return {"ok": False, "error": "no text extracted"}

    return ingest_text(title, text, source=p.name,
                       extra_meta={"path": str(p), "format": ext.lstrip(".")})


# ---------- fine-tuning corpus export ----------
def export_for_finetuning(out_path: str | Path) -> Path:
    """Dump every KB chunk as MLX-LM-compatible JSONL (`{"text": "..."}`).

    Run on the CLI:
        python -m src.kb.export → .cache/finetune_corpus.jsonl

    Then fine-tune DeepSeek-R1 with MLX-LM:
        python -m mlx_lm.lora --model deepseek-ai/DeepSeek-R1-Distill-Llama-8B \\
            --train --data .cache/finetune_corpus.jsonl \\
            --iters 600 --batch-size 1
    """
    import json
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    kb = KnowledgeBase.get()
    n = 0
    with out_path.open("w") as fh:
        # Pull every chunk directly from the SQLite KB
        try:
            rows = kb._conn.execute(
                "SELECT title, source, text, meta FROM doc_chunks"
            ).fetchall()
        except Exception as e:
            log.warning(f"export_for_finetuning: KB read failed: {e}")
            rows = []
        for r in rows:
            text = r["text"]
            if not text or not text.strip():
                continue
            try:
                meta = json.loads(r["meta"] or "{}")
            except Exception:
                meta = {}
            meta.update({"title": r["title"], "source": r["source"]})
            fh.write(json.dumps({"text": text, "meta": meta},
                                ensure_ascii=False) + "\n")
            n += 1
    log.info(f"exported {n} chunks → {out_path}")
    return out_path

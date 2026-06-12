"""Download & extract NSE/BSE filing PDFs (annual reports, earnings-call
transcripts, investor presentations) that screener.in surfaces in its
`recent_documents` list.

Used by the D-R1-Quant funnel's Stage 2 to give the LLM real primary-source
material, and to ingest those documents into the Knowledge Base.

Slow (PDF download + text extraction), so:
  - capped to the N most relevant docs per stock
  - results cached on disk
  - only invoked by the funnel (20 stocks), never the universe map (3000)
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

import requests

from config import settings
from src.utils.logger import get_logger

log = get_logger("tools.documents")

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# Priority — most analytically useful filing types first.
_DOC_PRIORITY = [
    ("annual report",        100),
    ("financial year",        95),
    ("financial result",      90),
    ("earnings call",         85),
    ("concall",               85),
    ("investor presentation", 80),
    ("analyst",               70),
    ("acquisition",           60),
    ("media release",         40),
]


def _doc_dir() -> Path:
    d = settings.cache_dir / "documents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rank(title: str) -> int:
    t = (title or "").lower()
    for kw, score in _DOC_PRIORITY:
        if kw in t:
            return score
    return 10


def _download(url: str) -> Optional[Path]:
    h = hashlib.sha1(url.encode()).hexdigest()[:16]
    path = _doc_dir() / f"{h}.pdf"
    if path.exists() and path.stat().st_size > 1000:
        return path
    try:
        r = requests.get(url, headers=UA, timeout=45)
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            path.write_bytes(r.content)
            return path
        log.debug(f"doc download {url} → {r.status_code}, "
                  f"{'PDF' if r.content[:4] == b'%PDF' else 'not-pdf'}")
    except Exception as e:
        log.debug(f"doc download failed {url}: {e}")
    return None


def _extract_text(path: Path, max_chars: int = 12000) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        chunks = []
        for page in reader.pages[:40]:        # cap pages — filings can be huge
            t = page.extract_text() or ""
            if t.strip():
                chunks.append(t)
            if sum(len(c) for c in chunks) > max_chars:
                break
        text = re.sub(r"\s+\n", "\n", "\n".join(chunks))
        return text[:max_chars]
    except Exception as e:
        log.debug(f"pdf extract failed {path.name}: {e}")
        return ""


def fetch_documents_multisource(
    symbol: str,
    fundamentals: Optional[dict] = None,
    max_docs: int = 2,
    ingest_kb: bool = True,
) -> list[dict]:
    """Aggregate document URLs from EVERY available source, then download
    the top `max_docs`. Sources merged (in order of preference):

      1. screener.in `recent_documents`     (when reachable)
      2. NSE `annual-reports` endpoint
      3. NSE `corporate-announcements`      (catalysts often link PDFs too)

    Returns the same [{title, url, text, chars}] as `fetch_documents`. Empty
    if every source returned zero — but you'll see a log line saying so.
    """
    candidates: list[dict] = []

    # 1. screener.in (from the already-fetched fundamentals dict)
    if fundamentals:
        for d in (fundamentals.get("recent_documents") or []):
            if d.get("url"):
                candidates.append(d)

    # 2. NSE annual reports
    try:
        from src.data.nse_scraper import fetch_annual_reports
        for ar in fetch_annual_reports(symbol):
            if ar.get("url"):
                candidates.append({
                    "title": f"Annual Report {ar.get('year', '')}".strip(),
                    "url": ar["url"],
                })
    except Exception as e:
        log.debug(f"NSE annual reports for {symbol}: {e}")

    # dedupe by URL
    seen, deduped = set(), []
    for c in candidates:
        u = c.get("url")
        if u and u not in seen:
            seen.add(u)
            deduped.append(c)

    if not deduped:
        log.info(f"  no document URLs for {symbol} from any source")
        return []
    log.info(f"  found {len(deduped)} candidate documents for {symbol}")
    return fetch_documents(deduped, symbol=symbol,
                           max_docs=max_docs, ingest_kb=ingest_kb)


def fetch_documents(
    documents: list[dict],
    symbol: str = "",
    max_docs: int = 2,
    ingest_kb: bool = True,
) -> list[dict]:
    """Given screener.in's `recent_documents` list, download + extract the
    top `max_docs` most relevant filings.

    Returns [{title, url, text, chars}]. Optionally ingests each into the KB.
    """
    if not documents:
        return []
    ranked = sorted(documents, key=lambda d: _rank(d.get("title", "")), reverse=True)
    out = []
    for doc in ranked[:max_docs]:
        url = doc.get("url")
        title = doc.get("title", "")
        if not url:
            continue
        log.info(f"  ⤓ {symbol}: fetching '{title[:60]}'")
        pdf = _download(url)
        if not pdf:
            continue
        text = _extract_text(pdf)
        if not text or len(text) < 200:
            continue
        out.append({"title": title, "url": url, "text": text, "chars": len(text)})

        if ingest_kb:
            try:
                from src.kb import ingest_text
                ingest_text(
                    title=f"{symbol} — {title[:80]}",
                    text=text,
                    source=f"filing:{url}",
                    extra_meta={"symbol": symbol, "doc_type": "filing"},
                )
            except Exception as e:
                log.debug(f"KB ingest failed for {symbol} doc: {e}")
    return out

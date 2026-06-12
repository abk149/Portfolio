"""Knowledge Base — SQLite + FTS5. Zero native dependencies.

We previously used ChromaDB, but its Rust core caused persistent install /
version breakage on this M-series Mac ('RustBindingsAPI' object has no
attribute 'bindings'). SQLite's FTS5 full-text search ships INSIDE Python's
stdlib `sqlite3` — nothing to pip-install, nothing to compile, nothing to
version-mismatch.

Trade-off: FTS5 is keyword search, not vector/semantic search. For a curated
finance corpus ("passages about the Kelly criterion", "why we REJECTED
high-P/E names") keyword search is entirely adequate — and it is bulletproof.

Three logical stores, all in one .sqlite file:
  • doc_chunks  — uploaded books / filings, chunked + full-text indexed
  • decisions   — every agent verdict (the auto-RAG training corpus)
  • universe    — per-stock technical + fundamental snapshots (the funnel reads these)
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import settings
from src.utils.logger import get_logger

log = get_logger("kb")


def _id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _fts_query(q: str) -> str:
    """Sanitise a user query for an FTS5 MATCH. We strip FTS operators and
    OR the surviving terms so partial matches still rank."""
    terms = re.findall(r"[A-Za-z0-9]+", q or "")
    if not terms:
        return '""'
    return " OR ".join(f'"{t}"' for t in terms)


# ─────────── tiny per-table counter so callers can do kb.universe.count() ───────────
class _Counter:
    def __init__(self, kb: "KnowledgeBase", table: str):
        self._kb, self._table = kb, table

    def count(self) -> int:
        return self._kb._count(self._table)


class KnowledgeBase:
    _shared: Optional["KnowledgeBase"] = None
    _lock = threading.Lock()

    def __init__(self):
        self.kb_dir = settings.chroma_dir / "kb"   # reuse the configured dir
        self.kb_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.kb_dir / "kb.sqlite"
        # check_same_thread=False — we serialise writes with self._lock
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._has_fts = self._init_schema()
        self.col = _Counter(self, "doc_chunks")
        self.decisions = _Counter(self, "decisions")
        self.universe = _Counter(self, "universe")
        log.info(
            f"KnowledgeBase (SQLite{'+FTS5' if self._has_fts else ', LIKE-search'}) "
            f"at {self.db_path} — {self._count('doc_chunks')} chunks · "
            f"{self._count('decisions')} decisions · "
            f"{self._count('universe')} universe stocks"
        )

    # ---------- schema ----------
    def _init_schema(self) -> bool:
        c = self._conn
        c.executescript("""
        CREATE TABLE IF NOT EXISTS doc_chunks (
            id TEXT PRIMARY KEY, doc_id TEXT, title TEXT, source TEXT,
            chunk_idx INTEGER, text TEXT, meta TEXT, embedding BLOB
        );
        CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc ON doc_chunks(doc_id);

        CREATE TABLE IF NOT EXISTS decisions (
            id TEXT PRIMARY KEY, symbol TEXT, verdict TEXT,
            prompt TEXT, response TEXT, meta TEXT, ts TEXT, embedding BLOB
        );
        CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions(symbol);

        CREATE TABLE IF NOT EXISTS universe (
            symbol TEXT PRIMARY KEY, name TEXT, sector TEXT, industry TEXT,
            tech_score REAL, fund_score REAL, combined REAL, recommendation TEXT,
            pe REAL, roe REAL, de REAL, market_cap_cr REAL,
            updated_at TEXT, data TEXT
        );
        """)
        c.commit()
        # Migrate older DBs that pre-date the embedding column.
        for tbl in ("doc_chunks", "decisions"):
            try:
                c.execute(f"ALTER TABLE {tbl} ADD COLUMN embedding BLOB")
                c.commit()
            except sqlite3.OperationalError:
                pass   # column already exists
        # FTS5 — ships in most Python sqlite builds; degrade to LIKE if absent.
        try:
            c.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks_fts
                USING fts5(text, title, content='doc_chunks', content_rowid='rowid');
            CREATE VIRTUAL TABLE IF NOT EXISTS decisions_fts
                USING fts5(prompt, response, symbol, content='decisions', content_rowid='rowid');
            """)
            c.commit()
            return True
        except sqlite3.OperationalError as e:
            log.warning(f"FTS5 unavailable ({e}) — falling back to LIKE search")
            return False

    def _count(self, table: str) -> int:
        try:
            return self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except Exception:
            return 0

    @classmethod
    def get(cls) -> "KnowledgeBase":
        if cls._shared is None:
            with cls._lock:
                if cls._shared is None:
                    try:
                        cls._shared = cls()
                    except Exception as e:
                        log.warning(f"KnowledgeBase init failed ({e}) — NO-OP mode")
                        cls._shared = _NullKB()
        return cls._shared

    # ---------- uploaded documents / book chunks ----------
    def add_chunks(self, doc_id: str, source: str, title: str,
                   chunks: list[str], extra_meta: Optional[dict] = None) -> int:
        if not chunks:
            return 0
        meta = json.dumps(extra_meta or {}, default=str)
        from src.kb import embeddings as _emb
        with self._lock:
            cur = self._conn.cursor()
            for i, text in enumerate(chunks):
                cid = _id(doc_id, str(i))
                vec = _emb.to_blob(_emb.embed(text))     # semantic vector (or None)
                cur.execute(
                    "INSERT OR REPLACE INTO doc_chunks "
                    "(id, doc_id, title, source, chunk_idx, text, meta, embedding) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (cid, doc_id, title, source, i, text, meta, vec),
                )
                if self._has_fts:
                    cur.execute(
                        "INSERT INTO doc_chunks_fts (rowid, text, title) "
                        "VALUES ((SELECT rowid FROM doc_chunks WHERE id=?), ?, ?)",
                        (cid, text, title),
                    )
            self._conn.commit()
        log.info(f"+ {len(chunks)} chunks from {title}")
        return len(chunks)

    def delete_doc(self, doc_id: str) -> int:
        with self._lock:
            cur = self._conn.cursor()
            n = cur.execute("SELECT COUNT(*) FROM doc_chunks WHERE doc_id=?",
                            (doc_id,)).fetchone()[0]
            cur.execute("DELETE FROM doc_chunks WHERE doc_id=?", (doc_id,))
            if self._has_fts:
                # FTS rows orphaned — rebuild is cheap for our scale
                cur.execute("INSERT INTO doc_chunks_fts(doc_chunks_fts) VALUES('rebuild')")
            self._conn.commit()
        return n

    def search(self, query: str, k: int = 5) -> list[dict]:
        """Hybrid retrieval:
          1. embed the query (Ollama) — if embeddings are available
          2. pull a candidate POOL (FTS5 keyword pre-filter, or all rows)
          3. re-rank the pool by cosine similarity
          4. drop anything below `kb_min_similarity` so the LLM only ever
             sees genuinely-relevant context — never padding.
        Falls back to pure FTS5/LIKE keyword search if no embeddings."""
        if self._count("doc_chunks") == 0:
            return []
        from src.kb import embeddings as _emb
        try:
            qvec = _emb.embed(query)
            if qvec is not None:
                # candidate pool: FTS5 keyword hits (wide) + recency fallback
                pool = []
                if self._has_fts:
                    pool = self._conn.execute(
                        "SELECT d.id, d.title, d.source, d.chunk_idx, d.text, d.embedding "
                        "FROM doc_chunks_fts f JOIN doc_chunks d ON d.rowid=f.rowid "
                        "WHERE doc_chunks_fts MATCH ? LIMIT 80",
                        (_fts_query(query),),
                    ).fetchall()
                if len(pool) < k * 4:
                    # widen — keyword pre-filter too narrow; rank everything
                    pool = self._conn.execute(
                        "SELECT id, title, source, chunk_idx, text, embedding "
                        "FROM doc_chunks WHERE embedding IS NOT NULL LIMIT 5000"
                    ).fetchall()
                ranked = _emb.cosine_rank(
                    qvec, [(dict(r), r["embedding"]) for r in pool])
                out = []
                for payload, sim in ranked:
                    if sim < settings.kb_min_similarity:
                        break                      # ranked desc — rest are worse
                    payload.pop("embedding", None)
                    payload["similarity"] = round(sim, 3)
                    out.append(payload)
                    if len(out) >= k:
                        break
                if out:
                    return out
                # nothing cleared the bar → fall through to keyword search

            # keyword-only path
            if self._has_fts:
                rows = self._conn.execute(
                    "SELECT title, source, chunk_idx, text "
                    "FROM doc_chunks_fts f JOIN doc_chunks d ON d.rowid=f.rowid "
                    "WHERE doc_chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                    (_fts_query(query), k),
                ).fetchall()
            else:
                like = f"%{query}%"
                rows = self._conn.execute(
                    "SELECT title, source, chunk_idx, text FROM doc_chunks "
                    "WHERE text LIKE ? LIMIT ?", (like, k),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            log.debug(f"kb search failed: {e}")
            return []

    def documents(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT doc_id, title, source, COUNT(*) AS chunks "
            "FROM doc_chunks GROUP BY doc_id ORDER BY title"
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------- agent decisions (auto-RAG training corpus) ----------
    def record_decision(self, *, symbol: str, prompt: str, response: str,
                        verdict: str, meta: Optional[dict] = None) -> None:
        if not symbol or not (response or "").strip():
            return
        rid = _id("decision", symbol, prompt[:200])
        ts = datetime.now(timezone.utc).isoformat()
        from src.kb import embeddings as _emb
        # Embed a compact "symbol + verdict + thesis" view — that's what a
        # later semantic query ("why did we reject high-PE names") matches on.
        vec = _emb.to_blob(_emb.embed(f"{symbol} {verdict} {response[:1500]}"))
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO decisions "
                "(id, symbol, verdict, prompt, response, meta, ts, embedding) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (rid, symbol, verdict, prompt, response,
                 json.dumps(meta or {}, default=str), ts, vec),
            )
            if self._has_fts:
                cur.execute(
                    "INSERT INTO decisions_fts (rowid, prompt, response, symbol) "
                    "VALUES ((SELECT rowid FROM decisions WHERE id=?), ?, ?, ?)",
                    (rid, prompt, response, symbol),
                )
            self._conn.commit()

    def search_decisions(self, query: str, k: int = 5) -> list[dict]:
        if self._count("decisions") == 0:
            return []
        from src.kb import embeddings as _emb

        def _shape(r) -> dict:
            d = dict(r)
            d.pop("embedding", None)
            m = json.loads(d.pop("meta", "{}") or "{}")
            d["text"] = f"Stock: {d['symbol']}\nVerdict: {d['verdict']}\n{d['response']}"
            d.update({k2: v for k2, v in m.items()})
            return d

        try:
            qvec = _emb.embed(query)
            if qvec is not None:
                pool = self._conn.execute(
                    "SELECT symbol, verdict, prompt, response, meta, ts, embedding "
                    "FROM decisions WHERE embedding IS NOT NULL LIMIT 5000"
                ).fetchall()
                ranked = _emb.cosine_rank(
                    qvec, [(dict(r), r["embedding"]) for r in pool])
                out = []
                for payload, sim in ranked:
                    if sim < settings.kb_min_similarity:
                        break
                    d = _shape(payload)
                    d["similarity"] = round(sim, 3)
                    out.append(d)
                    if len(out) >= k:
                        break
                if out:
                    return out

            # keyword fallback
            if self._has_fts:
                rows = self._conn.execute(
                    "SELECT d.symbol, d.verdict, d.prompt, d.response, d.meta, d.ts "
                    "FROM decisions_fts f JOIN decisions d ON d.rowid=f.rowid "
                    "WHERE decisions_fts MATCH ? ORDER BY rank LIMIT ?",
                    (_fts_query(query), k),
                ).fetchall()
            else:
                like = f"%{query}%"
                rows = self._conn.execute(
                    "SELECT symbol, verdict, prompt, response, meta, ts FROM decisions "
                    "WHERE prompt LIKE ? OR response LIKE ? LIMIT ?",
                    (like, like, k),
                ).fetchall()
            return [_shape(r) for r in rows]
        except Exception as e:
            log.debug(f"decision search failed: {e}")
            return []

    def export_decisions(self, out_path) -> int:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        rows = self._conn.execute(
            "SELECT prompt, response, meta FROM decisions"
        ).fetchall()
        n = 0
        with out_path.open("w") as fh:
            for r in rows:
                fh.write(json.dumps({
                    "prompt": r["prompt"], "completion": r["response"],
                    "meta": json.loads(r["meta"] or "{}"),
                }) + "\n")
                n += 1
        return n

    # ---------- universe store (the persistent stock database) ----------
    def upsert_stock(self, symbol: str, data: dict) -> None:
        sym = symbol.upper()
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO universe "
                "(symbol, name, sector, industry, tech_score, fund_score, "
                " combined, recommendation, pe, roe, de, market_cap_cr, "
                " updated_at, data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sym, data.get("name"), data.get("sector"), data.get("industry"),
                 data.get("tech_score"), data.get("fund_score"),
                 data.get("combined"), data.get("recommendation"),
                 data.get("PE") if data.get("PE") is not None else data.get("pe"),
                 data.get("ROE") if data.get("ROE") is not None else data.get("roe_pct"),
                 data.get("DE") if data.get("DE") is not None else data.get("debt_to_equity"),
                 data.get("market_cap_cr"),
                 now, json.dumps(data, default=str)),
            )
            self._conn.commit()

    def get_stock(self, symbol: str) -> Optional[dict]:
        r = self._conn.execute(
            "SELECT * FROM universe WHERE symbol=?", (symbol.upper(),)
        ).fetchone()
        if not r:
            return None
        d = dict(r)
        try:
            d["_data"] = json.loads(d.get("data") or "{}")
        except Exception:
            d["_data"] = {}
        return d

    def stock_age_days(self, symbol: str) -> Optional[float]:
        r = self._conn.execute(
            "SELECT updated_at FROM universe WHERE symbol=?", (symbol.upper(),)
        ).fetchone()
        if not r or not r["updated_at"]:
            return None
        try:
            ts = datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - ts).total_seconds() / 86400
        except Exception:
            return None

    def search_stocks(self, query: str, k: int = 10) -> list[dict]:
        like = f"%{query}%"
        rows = self._conn.execute(
            "SELECT symbol, name, sector, tech_score, fund_score, combined, "
            "recommendation, pe, roe FROM universe "
            "WHERE symbol LIKE ? OR name LIKE ? OR sector LIKE ? LIMIT ?",
            (like, like, like, k),
        ).fetchall()
        return [dict(r) for r in rows]

    def all_stocks(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM universe").fetchall()
        return [dict(r) for r in rows]

    # ---------- stats ----------
    def stats(self) -> dict:
        try:
            from src.kb import embeddings as _emb
            semantic = _emb.is_available()
        except Exception:
            semantic = False
        return {
            "chunks": self._count("doc_chunks"),
            "documents": len(self.documents()),
            "decisions": self._count("decisions"),
            "universe_stocks": self._count("universe"),
            "path": str(self.db_path),
            "fts5": self._has_fts,
            "semantic_search": semantic,
            "search_mode": "semantic (Ollama embeddings) + keyword"
                           if semantic else
                           ("keyword (FTS5)" if self._has_fts else "keyword (LIKE)"),
        }


# ─────────── no-op fallback (kept for absolute safety) ───────────
class _NullColl:
    def count(self): return 0


class _NullKB:
    col = decisions = universe = _NullColl()
    db_path = "(disabled)"
    _has_fts = False
    def add_chunks(self, *a, **k): return 0
    def delete_doc(self, *a, **k): return 0
    def search(self, *a, **k): return []
    def documents(self): return []
    def record_decision(self, *a, **k): pass
    def search_decisions(self, *a, **k): return []
    def export_decisions(self, *a, **k): return 0
    def upsert_stock(self, *a, **k): pass
    def get_stock(self, *a, **k): return None
    def stock_age_days(self, *a, **k): return None
    def search_stocks(self, *a, **k): return []
    def all_stocks(self): return []
    def stats(self):
        return {"chunks": 0, "documents": 0, "decisions": 0,
                "universe_stocks": 0, "path": "(KB disabled)", "fts5": False}

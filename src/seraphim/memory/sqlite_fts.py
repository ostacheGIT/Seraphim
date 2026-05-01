"""SQLite FTS5 memory backend — zero extra dependencies."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from seraphim.memory._stubs import MemoryBackend, RetrievalResult


class SQLiteFTSMemory(MemoryBackend):
    """Full-text search backed by SQLite FTS5 with BM25 ranking."""

    backend_id = "sqlite_fts"

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._path = db_path or (Path.home() / ".seraphim" / "rag.db")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._init_db()

    def _init_db(self) -> None:
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS rag_documents (
                    id       TEXT PRIMARY KEY,
                    source   TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
            """)
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS rag_fts
                USING fts5(id UNINDEXED, content, tokenize='porter ascii')
            """)

    def store(
        self,
        content: str,
        *,
        source: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        doc_id = uuid.uuid4().hex
        with self._conn:
            self._conn.execute(
                "INSERT INTO rag_documents (id, source, metadata) VALUES (?, ?, ?)",
                (doc_id, source, json.dumps(metadata or {})),
            )
            self._conn.execute(
                "INSERT INTO rag_fts (id, content) VALUES (?, ?)",
                (doc_id, content),
            )
        return doc_id

    def retrieve(self, query: str, *, top_k: int = 5, **kwargs: Any) -> List[RetrievalResult]:
        if not query.strip():
            return []
        import re
        # Strip punctuation, filter stopwords, build OR query for FTS5
        _STOPWORDS = {
            "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "shall", "can", "need", "dare", "ought",
            "what", "which", "who", "whom", "whose", "when", "where", "why", "how",
            "this", "that", "these", "those", "i", "me", "my", "we", "our", "you",
            "your", "he", "she", "it", "they", "them", "their", "of", "in", "on",
            "at", "by", "for", "with", "about", "to", "from", "into", "through",
            "and", "or", "but", "if", "as", "so", "not", "no", "nor",
            "le", "la", "les", "un", "une", "des", "de", "du", "au", "aux",
            "je", "tu", "il", "elle", "nous", "vous", "ils", "elles", "en",
            "est", "sont", "qui", "que", "quoi", "quel", "quelle", "quels",
        }
        raw_tokens = re.sub(r'[^\w\s]', ' ', query).lower().split()
        terms = [t for t in raw_tokens if t and t not in _STOPWORDS and len(t) > 1]
        # Fall back to all tokens if every word was a stopword
        if not terms:
            terms = [t for t in raw_tokens if t]
        if not terms:
            return []
        safe_query = " OR ".join(terms)
        try:
            cursor = self._conn.execute(
                """
                SELECT f.id, f.content, d.source, d.metadata, bm25(rag_fts) AS score
                FROM rag_fts f
                JOIN rag_documents d ON d.id = f.id
                WHERE rag_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (safe_query, top_k),
            )
        except sqlite3.OperationalError:
            return []
        results = []
        for row in cursor.fetchall():
            doc_id, content, source, meta_json, score = row
            results.append(RetrievalResult(
                content=content,
                score=float(-score),  # bm25() returns negative values in SQLite FTS5
                source=source,
                metadata=json.loads(meta_json),
            ))
        return results

    def delete(self, doc_id: str) -> bool:
        cur = self._conn.execute("SELECT 1 FROM rag_documents WHERE id = ?", (doc_id,))
        if cur.fetchone() is None:
            return False
        with self._conn:
            self._conn.execute("DELETE FROM rag_documents WHERE id = ?", (doc_id,))
            self._conn.execute("DELETE FROM rag_fts WHERE id = ?", (doc_id,))
        return True

    def clear(self) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM rag_documents")
            self._conn.execute("DELETE FROM rag_fts")

    def count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM rag_documents")
        return cur.fetchone()[0]

    def close(self) -> None:
        self._conn.close()
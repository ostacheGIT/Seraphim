"""BM25 sparse retrieval backend using rank_bm25."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple

from seraphim.memory._stubs import MemoryBackend, RetrievalResult


class BM25Memory(MemoryBackend):
    """In-memory BM25 (Okapi) retrieval via rank_bm25."""

    backend_id = "bm25"

    def __init__(self) -> None:
        try:
            from rank_bm25 import BM25Okapi
            self._BM25Okapi = BM25Okapi
        except ImportError as exc:
            raise ImportError(
                "rank-bm25 required. Install: uv add rank-bm25"
            ) from exc
        self._documents: Dict[str, Tuple[str, str, Dict[str, Any]]] = {}
        self._id_order: List[str] = []
        self._bm25 = None

    def _rebuild(self) -> None:
        if not self._id_order:
            self._bm25 = None
            return
        corpus = [self._documents[doc_id][0].lower().split() for doc_id in self._id_order]
        self._bm25 = self._BM25Okapi(corpus)

    def store(
        self,
        content: str,
        *,
        source: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        doc_id = uuid.uuid4().hex
        self._documents[doc_id] = (content, source, metadata or {})
        self._id_order.append(doc_id)
        self._rebuild()
        return doc_id

    def retrieve(self, query: str, *, top_k: int = 5, **kwargs: Any) -> List[RetrievalResult]:
        if not query.strip() or self._bm25 is None:
            return []
        tokenized = query.lower().split()
        scores = self._bm25.get_scores(tokenized)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
        results = []
        for idx, score in ranked:
            if score <= 0:
                continue
            doc_id = self._id_order[idx]
            content, source, meta = self._documents[doc_id]
            results.append(RetrievalResult(
                content=content,
                score=float(score),
                source=source,
                metadata=dict(meta),
            ))
        return results

    def delete(self, doc_id: str) -> bool:
        if doc_id not in self._documents:
            return False
        del self._documents[doc_id]
        self._id_order.remove(doc_id)
        self._rebuild()
        return True

    def clear(self) -> None:
        self._documents.clear()
        self._id_order.clear()
        self._bm25 = None

    def count(self) -> int:
        return len(self._id_order)
"""FAISS dense retrieval backend with cosine similarity."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from seraphim.memory._stubs import MemoryBackend, RetrievalResult
from seraphim.memory.embeddings import Embedder, SentenceTransformerEmbedder


class FAISSMemory(MemoryBackend):
    """Dense retrieval via FAISS IndexFlatIP on L2-normalised vectors."""

    backend_id = "faiss"

    def __init__(self, embedder: Optional[Embedder] = None) -> None:
        try:
            import faiss
        except ImportError as exc:
            raise ImportError(
                "faiss-cpu required. Install: uv add faiss-cpu"
            ) from exc
        if embedder is None:
            embedder = SentenceTransformerEmbedder()
        self._embedder = embedder
        self._faiss = faiss
        self._index = faiss.IndexFlatIP(self._embedder.dim())
        self._documents: Dict[str, Tuple[str, str, Dict[str, Any]]] = {}
        self._id_map: List[str] = []
        self._deleted: Set[str] = set()

    def store(
        self,
        content: str,
        *,
        source: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        doc_id = uuid.uuid4().hex
        vec = self._embedder.embed([content])
        self._faiss.normalize_L2(vec)
        self._index.add(vec)
        self._documents[doc_id] = (content, source, metadata or {})
        self._id_map.append(doc_id)
        return doc_id

    def retrieve(self, query: str, *, top_k: int = 5, **kwargs: Any) -> List[RetrievalResult]:
        if not query.strip() or self._index.ntotal == 0:
            return []
        vec = self._embedder.embed([query])
        self._faiss.normalize_L2(vec)
        k = min(top_k + len(self._deleted), self._index.ntotal)
        scores, indices = self._index.search(vec, k)
        results: List[RetrievalResult] = []
        for score, idx in zip(scores[0].tolist(), indices[0].tolist()):
            if idx < 0:
                continue
            doc_id = self._id_map[idx]
            if doc_id in self._deleted:
                continue
            content, source, meta = self._documents[doc_id]
            results.append(RetrievalResult(
                content=content,
                score=float(score),
                source=source,
                metadata=dict(meta),
            ))
            if len(results) >= top_k:
                break
        return results

    def delete(self, doc_id: str) -> bool:
        if doc_id not in self._documents or doc_id in self._deleted:
            return False
        self._deleted.add(doc_id)
        return True

    def clear(self) -> None:
        self._index.reset()
        self._documents.clear()
        self._id_map.clear()
        self._deleted.clear()

    def count(self) -> int:
        return self._index.ntotal - len(self._deleted)
"""Hybrid memory backend — Reciprocal Rank Fusion of sparse + dense retrievers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from seraphim.memory._stubs import MemoryBackend, RetrievalResult


def reciprocal_rank_fusion(
    ranked_lists: List[List[RetrievalResult]],
    *,
    k: int = 60,
    weights: Optional[List[float]] = None,
) -> List[RetrievalResult]:
    """Fuse ranked lists with RRF: score(d) = sum(weight_i / (k + rank_i(d)))."""
    if weights is None:
        weights = [1.0] * len(ranked_lists)

    scores: Dict[str, float] = {}
    best_result: Dict[str, RetrievalResult] = {}

    for weight, results in zip(weights, ranked_lists):
        for rank, result in enumerate(results):
            key = result.content
            scores[key] = scores.get(key, 0.0) + weight / (k + rank + 1)
            if key not in best_result:
                best_result[key] = result

    return [
        RetrievalResult(
            content=best_result[key].content,
            score=fused_score,
            source=best_result[key].source,
            metadata=best_result[key].metadata,
        )
        for key, fused_score in sorted(scores.items(), key=lambda x: x[1], reverse=True)
    ]


class HybridMemory(MemoryBackend):
    """Fuses BM25 (sparse) and FAISS (dense) via Reciprocal Rank Fusion."""

    backend_id = "hybrid"

    def __init__(
        self,
        *,
        sparse: MemoryBackend,
        dense: MemoryBackend,
        k: int = 60,
        sparse_weight: float = 1.0,
        dense_weight: float = 1.0,
    ) -> None:
        self._sparse = sparse
        self._dense = dense
        self._k = k
        self._weights = [sparse_weight, dense_weight]
        self._id_map: Dict[str, str] = {}

    def store(
        self,
        content: str,
        *,
        source: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        sparse_id = self._sparse.store(content, source=source, metadata=metadata)
        dense_id = self._dense.store(content, source=source, metadata=metadata)
        self._id_map[sparse_id] = dense_id
        return sparse_id

    def retrieve(self, query: str, *, top_k: int = 5, **kwargs: Any) -> List[RetrievalResult]:
        fetch_k = top_k * 3
        sparse_results = self._sparse.retrieve(query, top_k=fetch_k)
        dense_results = self._dense.retrieve(query, top_k=fetch_k)
        fused = reciprocal_rank_fusion(
            [sparse_results, dense_results],
            k=self._k,
            weights=self._weights,
        )
        return fused[:top_k]

    def delete(self, doc_id: str) -> bool:
        sparse_ok = self._sparse.delete(doc_id)
        dense_id = self._id_map.pop(doc_id, None)
        dense_ok = self._dense.delete(dense_id) if dense_id else False
        return sparse_ok or dense_ok

    def clear(self) -> None:
        self._sparse.clear()
        self._dense.clear()
        self._id_map.clear()

    def count(self) -> int:
        return getattr(self._sparse, "count", lambda: 0)()
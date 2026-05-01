"""Embedder abstraction: SentenceTransformer and Ollama backends."""

from __future__ import annotations

import concurrent.futures
from abc import ABC, abstractmethod
from typing import Any, List, Optional


class Embedder(ABC):
    @abstractmethod
    def embed(self, texts: List[str]) -> Any:
        """Return a numpy array of shape (n, dim)."""

    @abstractmethod
    def dim(self) -> int:
        """Return embedding dimensionality."""


class SentenceTransformerEmbedder(Embedder):
    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers required. Install: uv add sentence-transformers"
            ) from exc
        self._model = SentenceTransformer(model_name)
        self._dim: int = self._model.get_sentence_embedding_dimension()

    def embed(self, texts: List[str]) -> Any:
        return self._model.encode(texts, convert_to_numpy=True)

    def dim(self) -> int:
        return self._dim


class OllamaEmbedder(Embedder):
    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str = "http://localhost:11434",
        *,
        batch_size: int = 16,
        max_parallel: int = 4,
        timeout_s: float = 120.0,
    ) -> None:
        import httpx
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._batch_size = max(1, batch_size)
        self._max_parallel = max(1, max_parallel)
        self._timeout_s = timeout_s
        self._httpx = httpx
        self._dim_cached: Optional[int] = None

    def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        resp = self._httpx.post(
            f"{self._base_url}/api/embed",
            json={"model": self._model, "input": texts},
            timeout=self._timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        embeddings = data.get("embeddings")
        if not embeddings:
            raise RuntimeError(f"Ollama returned no embeddings for model {self._model!r}")
        return embeddings

    def embed(self, texts: List[str]) -> Any:
        import numpy as np
        if not texts:
            return np.zeros((0, self.dim()), dtype=np.float32)
        batches = [texts[i:i + self._batch_size] for i in range(0, len(texts), self._batch_size)]
        results: List[Optional[List[List[float]]]] = [None] * len(batches)
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(self._max_parallel, len(batches))
        ) as pool:
            future_to_idx = {pool.submit(self._embed_batch, b): i for i, b in enumerate(batches)}
            for future in concurrent.futures.as_completed(future_to_idx):
                results[future_to_idx[future]] = future.result()
        flat: List[List[float]] = []
        for r in results:
            assert r is not None
            flat.extend(r)
        arr = np.asarray(flat, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        return arr / norms

    def dim(self) -> int:
        if self._dim_cached is None:
            vecs = self._embed_batch(["probe"])
            self._dim_cached = len(vecs[0])
        return self._dim_cached
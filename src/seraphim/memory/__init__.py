"""RAG memory system — pluggable backends with context injection."""

from __future__ import annotations

from typing import Optional

from seraphim.memory._stubs import MemoryBackend, RetrievalResult
from seraphim.memory.chunking import Chunk, ChunkConfig, chunk_text
from seraphim.memory.context import ContextConfig, format_context, inject_context
from seraphim.memory.ingest import ingest_directory, ingest_file, ingest_text
from seraphim.memory.sqlite_fts import SQLiteFTSMemory

_default_backend: Optional[MemoryBackend] = None


def get_rag_backend() -> Optional[MemoryBackend]:
    return _default_backend


def set_rag_backend(backend: Optional[MemoryBackend]) -> None:
    global _default_backend
    _default_backend = backend


def create_backend(backend_type: str = "sqlite_fts", **kwargs) -> MemoryBackend:
    """Factory: sqlite_fts | faiss | bm25 | hybrid."""
    if backend_type == "sqlite_fts":
        return SQLiteFTSMemory(**kwargs)
    if backend_type == "faiss":
        from seraphim.memory.faiss_backend import FAISSMemory
        return FAISSMemory(**kwargs)
    if backend_type == "bm25":
        from seraphim.memory.bm25_backend import BM25Memory
        return BM25Memory(**kwargs)
    if backend_type == "hybrid":
        from seraphim.memory.bm25_backend import BM25Memory
        from seraphim.memory.faiss_backend import FAISSMemory
        from seraphim.memory.hybrid_backend import HybridMemory
        sparse = kwargs.pop("sparse", BM25Memory())
        dense = kwargs.pop("dense", FAISSMemory())
        return HybridMemory(sparse=sparse, dense=dense, **kwargs)
    raise ValueError(
        f"Unknown RAG backend: {backend_type!r}. "
        "Options: sqlite_fts, faiss, bm25, hybrid"
    )


def init_rag() -> None:
    """Initialize the global RAG backend from settings."""
    from seraphim.settings import settings

    cfg = settings.memory
    if not cfg.rag_enabled:
        return

    backend = create_backend(cfg.rag_backend)
    set_rag_backend(backend)


__all__ = [
    "MemoryBackend",
    "RetrievalResult",
    "Chunk",
    "ChunkConfig",
    "chunk_text",
    "ContextConfig",
    "format_context",
    "inject_context",
    "ingest_text",
    "ingest_file",
    "ingest_directory",
    "SQLiteFTSMemory",
    "get_rag_backend",
    "set_rag_backend",
    "create_backend",
    "init_rag",
]
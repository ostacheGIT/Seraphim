"""File and text ingestion into a RAG memory backend."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from seraphim.memory._stubs import MemoryBackend
from seraphim.memory.chunking import ChunkConfig, chunk_text

_DEFAULT_EXTENSIONS = [".txt", ".md", ".rst", ".py", ".pdf"]


def ingest_text(
    text: str,
    backend: MemoryBackend,
    *,
    source: str = "",
    config: Optional[ChunkConfig] = None,
) -> List[str]:
    """Chunk text and store all chunks. Returns list of doc ids."""
    chunks = chunk_text(text, source=source, config=config)
    return [
        backend.store(
            chunk.content,
            source=source,
            metadata={"chunk_index": chunk.index, "offset": chunk.offset},
        )
        for chunk in chunks
    ]


def ingest_file(
    path: Path,
    backend: MemoryBackend,
    *,
    config: Optional[ChunkConfig] = None,
) -> List[str]:
    """Read a file (txt/md/pdf) and ingest it into the backend."""
    path = Path(path)
    source = path.name

    if path.suffix.lower() == ".pdf":
        try:
            import pdfplumber
        except ImportError as exc:
            raise ImportError(
                "pdfplumber required for PDF ingestion. Install: uv add pdfplumber"
            ) from exc
        parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    parts.append(t)
        text = "\n\n".join(parts)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")

    return ingest_text(text, backend, source=source, config=config)


def ingest_directory(
    directory: Path,
    backend: MemoryBackend,
    *,
    extensions: Optional[List[str]] = None,
    config: Optional[ChunkConfig] = None,
) -> int:
    """Recursively ingest all matching files. Returns total chunks stored."""
    directory = Path(directory)
    exts = set(extensions or _DEFAULT_EXTENSIONS)
    count = 0
    for path in directory.rglob("*"):
        if path.is_file() and path.suffix.lower() in exts:
            try:
                ids = ingest_file(path, backend, config=config)
                count += len(ids)
            except Exception:
                pass
    return count
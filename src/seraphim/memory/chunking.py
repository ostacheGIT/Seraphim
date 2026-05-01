"""Document chunking with configurable size and overlap."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class ChunkConfig:
    chunk_size: int = 512
    chunk_overlap: int = 64
    min_chunk_size: int = 50


@dataclass(slots=True)
class Chunk:
    content: str
    source: str = ""
    offset: int = 0
    index: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


def chunk_text(
    text: str,
    *,
    source: str = "",
    config: Optional[ChunkConfig] = None,
) -> List[Chunk]:
    """Split text into chunks respecting paragraph boundaries."""
    if not text or not text.strip():
        return []

    cfg = config or ChunkConfig()
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    chunks: List[Chunk] = []
    current_tokens: List[str] = []
    current_offset = 0
    chunk_start_offset = 0

    for para in paragraphs:
        para_tokens = para.split()

        if current_tokens and len(current_tokens) + len(para_tokens) > cfg.chunk_size:
            chunk_content = " ".join(current_tokens)
            if len(current_tokens) >= cfg.min_chunk_size:
                chunks.append(Chunk(
                    content=chunk_content,
                    source=source,
                    offset=chunk_start_offset,
                    index=len(chunks),
                ))
            if cfg.chunk_overlap > 0 and len(current_tokens) > cfg.chunk_overlap:
                current_tokens = list(current_tokens[-cfg.chunk_overlap:])
            else:
                current_tokens = []
            chunk_start_offset = current_offset

        if len(para_tokens) > cfg.chunk_size:
            if current_tokens:
                chunk_content = " ".join(current_tokens)
                if len(current_tokens) >= cfg.min_chunk_size:
                    chunks.append(Chunk(
                        content=chunk_content,
                        source=source,
                        offset=chunk_start_offset,
                        index=len(chunks),
                    ))
                current_tokens = []
            idx = 0
            while idx < len(para_tokens):
                window = para_tokens[idx:idx + cfg.chunk_size]
                if len(window) >= cfg.min_chunk_size:
                    chunks.append(Chunk(
                        content=" ".join(window),
                        source=source,
                        offset=current_offset + idx,
                        index=len(chunks),
                    ))
                idx += max(1, cfg.chunk_size - cfg.chunk_overlap)
            current_offset += len(para_tokens)
            chunk_start_offset = current_offset
            continue

        current_tokens.extend(para_tokens)
        current_offset += len(para_tokens)

    if current_tokens and len(current_tokens) >= cfg.min_chunk_size:
        chunks.append(Chunk(
            content=" ".join(current_tokens),
            source=source,
            offset=chunk_start_offset,
            index=len(chunks),
        ))

    return chunks
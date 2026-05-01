"""Base classes for RAG memory backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class RetrievalResult:
    content: str
    score: float = 0.0
    source: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class MemoryBackend(ABC):
    backend_id: str = "base"

    @abstractmethod
    def store(
        self,
        content: str,
        *,
        source: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Store content and return a unique doc id."""

    @abstractmethod
    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        **kwargs: Any,
    ) -> List[RetrievalResult]:
        """Search and return the top-k results."""

    @abstractmethod
    def delete(self, doc_id: str) -> bool:
        """Delete a document by id. Return True if it existed."""

    @abstractmethod
    def clear(self) -> None:
        """Remove all stored documents."""
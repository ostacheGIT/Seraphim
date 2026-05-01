"""Context injection — retrieve relevant docs and prepend to agent messages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from seraphim.memory._stubs import MemoryBackend, RetrievalResult


@dataclass
class ContextConfig:
    enabled: bool = True
    top_k: int = 5
    min_score: float = 0.0
    max_context_tokens: int = 2048


def _count_tokens(text: str) -> int:
    return len(text.split())


def format_context(results: List[RetrievalResult]) -> str:
    if not results:
        return ""
    lines = []
    for r in results:
        prefix = f"[Source: {r.source}] " if r.source else ""
        lines.append(f"{prefix}{r.content}")
    return "\n\n".join(lines)


def inject_context(
    query: str,
    messages: List[Dict[str, Any]],
    backend: MemoryBackend,
    *,
    config: Optional[ContextConfig] = None,
) -> List[Dict[str, Any]]:
    """Retrieve relevant context and prepend as a system message.

    Returns a new list — original messages are not mutated.
    If no results pass the score threshold, returns messages unchanged.
    """
    cfg = config or ContextConfig()
    if not cfg.enabled:
        return messages

    results = backend.retrieve(query, top_k=cfg.top_k)
    results = [r for r in results if r.score >= cfg.min_score]
    if not results:
        return messages

    truncated: List[RetrievalResult] = []
    total_tokens = 0
    for r in results:
        tokens = _count_tokens(r.content)
        if total_tokens + tokens > cfg.max_context_tokens:
            break
        truncated.append(r)
        total_tokens += tokens

    if not truncated:
        return messages

    ctx_text = format_context(truncated)
    ctx_message: Dict[str, Any] = {
        "role": "system",
        "content": (
            "The following context was retrieved from the knowledge base. "
            "Use it to inform your response:\n\n" + ctx_text
        ),
    }
    return [ctx_message] + list(messages)
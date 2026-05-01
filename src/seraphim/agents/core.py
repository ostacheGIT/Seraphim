"""Base classes partagées — sans import circulaire."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

from seraphim.engine import get_engine
from seraphim.engine.base import ChatMessage


@dataclass
class AgentContext:
    messages: list[ChatMessage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str) -> None:
        self.messages.append({"role": "assistant", "content": content})

    def add_system(self, content: str) -> None:
        # On garde ton comportement: le system en tête
        self.messages.insert(0, {"role": "system", "content": content})


class BaseAgent(ABC):
    name: str = "base"
    description: str = "Base agent"
    system_prompt: str = "You are Seraphim, a helpful personal AI assistant."

    def __init__(self, engine_id: Optional[str] = None) -> None:
        # On stocke seulement l'ID; le moteur réel est récupéré via get_engine
        self.engine_id = engine_id

    @property
    def engine(self):
        """
        Accès pratique au moteur LLM pour les agents concrets.

        Si engine_id est None, get_engine() renvoie le moteur par défaut
        (actuellement ollama_qwen3b).
        """
        return get_engine(self.engine_id)

    async def _chat(self, messages) -> str:
        """Call engine.chat() and extract the response string."""
        result = await self.engine.chat(messages)
        msgs = result.get("messages", [])
        return msgs[-1].get("content", "") if msgs else ""

    @abstractmethod
    async def run(self, query: str, context: AgentContext | None = None) -> str:
        ...

    def build_context(self, query: str, context: AgentContext | None = None) -> AgentContext:
        ctx = context or AgentContext()
        if not any(m.get("role") == "system" for m in ctx.messages):
            ctx.add_system(self.system_prompt)

        # RAG context injection — only if a backend is active
        try:
            from seraphim.memory import get_rag_backend, inject_context, ContextConfig
            from seraphim.settings import settings
            rag = get_rag_backend()
            if rag is not None:
                cfg = settings.memory
                ctx.messages = inject_context(
                    query,
                    ctx.messages,
                    rag,
                    config=ContextConfig(
                        top_k=cfg.context_top_k,
                        min_score=cfg.context_min_score,
                        max_context_tokens=cfg.context_max_tokens,
                    ),
                )
        except Exception:
            logger.warning("RAG context injection failed", exc_info=True)

        ctx.add_user(query)
        return ctx
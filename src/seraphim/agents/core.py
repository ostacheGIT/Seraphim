"""Base classes partagées — sans import circulaire."""

from __future__ import annotations

import functools
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
    session_id: str = ""

    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str) -> None:
        self.messages.append({"role": "assistant", "content": content})

    def add_system(self, content: str) -> None:
        for i, m in enumerate(self.messages):
            if m.get("role") == "system":
                self.messages[i] = {"role": "system", "content": content}
                return
        self.messages.insert(0, {"role": "system", "content": content})


def _auto_trace_wrapper(fn):
    @functools.wraps(fn)
    async def wrapper(self, query: str, context=None):
        from seraphim.learning.collector import TraceCollector
        _tracer = TraceCollector(self.name, query, getattr(context, "session_id", ""))
        try:
            result = await fn(self, query, context)
            _tracer.finish(result, success=True)
            await _tracer.save()
            return result
        except Exception as exc:
            _tracer.finish(f"ERROR: {exc}", success=False)
            await _tracer.save()
            raise
    return wrapper


class BaseAgent(ABC):
    name: str = "base"
    description: str = "Base agent"
    system_prompt: str = "You are Seraphim, a helpful personal AI assistant."
    _auto_trace: bool = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "run" in cls.__dict__ and getattr(cls, "_auto_trace", True):
            cls.run = _auto_trace_wrapper(cls.__dict__["run"])

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

    async def _chat_with_tools(self, messages, tools: list[dict]) -> tuple[str, list]:
        """Call engine with native tool schemas. Returns (text_content, tool_calls)."""
        if not tools:
            return await self._chat(messages), []
        result = await self.engine.chat(messages, tools=tools)
        msgs = result.get("messages", [])
        if not msgs:
            return "", []
        last = msgs[-1]
        return (last.get("content", "") or ""), (last.get("tool_calls", []) or [])

    @abstractmethod
    async def run(self, query: str, context: AgentContext | None = None) -> str:
        ...

    async def stream(self, query: str, context: "AgentContext | None" = None):
        """Default: run to completion, yield full result as one chunk. Override for true streaming."""
        yield await self.run(query, context)

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
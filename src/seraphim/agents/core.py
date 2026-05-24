"""Base classes partagées — sans import circulaire."""

from __future__ import annotations

import functools
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

from seraphim.engine import get_engine
from seraphim.engine.base import ChatMessage

# Heuristic: responses that start with these patterns are likely poor quality.
_BAD_RESPONSE_RE = re.compile(
    r"^\s*(?:"
    r"I(?:'m|\s+am)?\s+(?:sorry|unable|not\s+able)|"
    r"I\s+(?:don'?t|cannot|can'?t)\s+(?:help|answer|provide|access)|"
    r"Je\s+(?:suis\s+)?(?:désolé|incapable)|"
    r"Je\s+ne\s+(?:peux\s+pas|sais\s+pas)"
    r")",
    re.I,
)


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

    async def maybe_compress(
        self,
        engine,
        max_messages: int = 24,
        keep_recent: int = 8,
    ) -> None:
        """Compress old messages into an LLM summary when the context grows too long.

        Keeps system messages intact, summarizes the middle chunk, retains the
        most recent `keep_recent` non-system messages verbatim.
        """
        non_sys = [m for m in self.messages if m.get("role") != "system"]
        if len(non_sys) <= max_messages:
            return

        sys_msgs = [m for m in self.messages if m.get("role") == "system"]
        cut = len(non_sys) - keep_recent
        to_compress = non_sys[:cut]
        to_keep = non_sys[cut:]

        if len(to_compress) < 4:
            return

        history = "\n".join(
            f"{m['role'].upper()}: {(m.get('content') or '')[:400]}"
            for m in to_compress
        )
        summary = f"[{len(to_compress)} previous messages]"
        try:
            result = await engine.chat([
                {
                    "role": "system",
                    "content": (
                        "Summarize this conversation in 3-5 sentences. "
                        "Keep key facts, tool results, and important decisions."
                    ),
                },
                {"role": "user", "content": history},
            ])
            out = result.get("messages", [])
            if out:
                summary = out[-1].get("content", summary) or summary
        except Exception:
            pass

        self.messages = sys_msgs + [
            {"role": "system", "content": f"[Context summary: {summary}]"}
        ] + to_keep
        logger.debug(
            "Context compressed: %d → %d messages", len(non_sys), len(to_keep) + 1
        )


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

    # ── Auto-registration registry ────────────────────────────────────────────
    _REGISTRY: dict[str, type[BaseAgent]] = {}

    _EXCLUDED_FROM_REGISTRY = frozenset({"base", "skill", "builtin_skill"})

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "run" in cls.__dict__ and getattr(cls, "_auto_trace", True):
            cls.run = _auto_trace_wrapper(cls.__dict__["run"])
        # Auto-register concrete agents (exclude runtime/parameterised agents)
        agent_name = getattr(cls, "name", None)
        if agent_name and agent_name not in BaseAgent._EXCLUDED_FROM_REGISTRY:
            BaseAgent._REGISTRY[agent_name] = cls

    def __init__(self, engine_id: Optional[str] = None) -> None:
        self.engine_id = engine_id

    @property
    def engine(self):
        return get_engine(self.engine_id)

    async def _chat(self, messages) -> str:
        """Call engine and return the response string.
        Uses streaming internally so the read timeout applies per token."""
        eng = self.engine
        if hasattr(eng, "stream_chat_api"):
            chunks: list[str] = []
            async for chunk in eng.stream_chat_api(messages):
                chunks.append(chunk)
            return "".join(chunks)
        result = await eng.chat(messages)
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

    async def _maybe_retry_response(
        self,
        messages: list,
        response: str,
        min_length: int = 30,
        max_retries: int = 1,
        min_score: float = 0.65,
    ) -> str:
        """Quality gate: check response quality and retry once if it seems poor.

        Uses cheap heuristics first (length + bad-start regex). Only calls the
        LLM judge for borderline responses, so typical good responses pay zero
        extra cost.
        """
        stripped = response.strip()

        # Fast path — looks fine, return immediately
        if len(stripped) >= min_length and not _BAD_RESPONSE_RE.match(stripped):
            return response

        for attempt in range(max_retries):
            query = next(
                (m["content"] for m in reversed(messages) if m.get("role") == "user"),
                "",
            )
            try:
                from seraphim.learning.evaluator import score_response
                score = await score_response(query, response)
                if score >= min_score:
                    return response
                logger.info(
                    "[quality_gate] agent=%s attempt=%d score=%.2f — retrying",
                    self.name, attempt + 1, score,
                )
            except Exception:
                return response

            retry_messages = [
                *messages,
                {"role": "assistant", "content": response},
                {
                    "role": "user",
                    "content": "Your previous answer was incomplete or unclear. Please provide a more complete and accurate response.",
                },
            ]
            response = await self._chat(retry_messages)
            stripped = response.strip()
            if len(stripped) >= min_length and not _BAD_RESPONSE_RE.match(stripped):
                return response

        return response

    @abstractmethod
    async def run(self, query: str, context: AgentContext | None = None) -> str:
        ...

    async def stream(self, query: str, context: "AgentContext | None" = None):
        """Default: run to completion, yield full result as one chunk."""
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

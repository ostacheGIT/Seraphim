from __future__ import annotations

import json
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from seraphim.engine.base import ChatMessage, ChatResult, LLMEngine
from seraphim.engine.metrics import InferenceMetrics, parse_ollama_metrics


class OllamaEngine:
    """
    Implémentation LLMEngine pour Ollama via /api/generate.

    Utilise un modèle local (par ex. 'qwen2.5:3b') tel qu'affiché par /api/tags.
    """

    id = "ollama"
    name = "Ollama"

    def __init__(
            self,
            model: str = "qwen2.5:3b",
            base_url: str = "http://localhost:11434",
            timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.last_metrics: InferenceMetrics | None = None

    def _build_prompt(self, messages: List[ChatMessage]) -> str:
        parts: List[str] = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "") or ""
            if role == "system":
                parts.append(f"[SYSTEM] {content}\n")
            elif role == "user":
                parts.append(f"[USER] {content}\n")
            elif role == "assistant":
                parts.append(f"[ASSISTANT] {content}\n")
            else:
                parts.append(f"[{role.upper()}] {content}\n")
        return "\n".join(parts).strip()

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=5.0) as client:
                r = await client.get("/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> List[str]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10.0) as client:
            r = await client.get("/api/tags")
            r.raise_for_status()
            data = r.json()
        return [m["name"] for m in data.get("models", [])]

    async def chat(
            self,
            messages: List[ChatMessage],
            tools: Optional[List[Dict[str, Any]]] = None,
            **kwargs: Any,
    ) -> ChatResult:
        # Always use /api/chat — native chat template, better instruction following
        return await self._chat_api(messages, tools=tools, **kwargs)

    async def _chat_api(
            self,
            messages: List[ChatMessage],
            tools: Optional[List[Dict[str, Any]]] = None,
            **kwargs: Any,
    ) -> ChatResult:
        """Utilise /api/chat avec support natif du tool calling (Ollama ≥0.3)."""
        # Sanitize messages: strip tool_calls field from non-assistant roles (Ollama rejects them)
        clean_messages = []
        for m in messages:
            cm: Dict[str, Any] = {"role": m.get("role", "user"), "content": m.get("content", "") or ""}
            if m.get("role") == "assistant" and m.get("tool_calls"):
                cm["tool_calls"] = m["tool_calls"]
            if m.get("role") == "tool":
                cm["role"] = "tool"
            if m.get("name"):
                cm["name"] = m["name"]
            if m.get("tool_call_id"):
                cm["tool_call_id"] = m["tool_call_id"]
            clean_messages.append(cm)

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": clean_messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        # format kwarg (e.g. "json" or JSON schema dict) → structured output
        if "format" in kwargs:
            payload["format"] = kwargs.pop("format")
        if kwargs:
            payload.update(kwargs)

        t0 = time.perf_counter_ns()
        async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
        ) as client:
            r = await client.post("/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()

        self.last_metrics = parse_ollama_metrics(data, t0)
        message = data.get("message", {})
        content = message.get("content", "") or ""
        tool_calls = message.get("tool_calls", [])

        result_msg: ChatMessage = {"role": "assistant", "content": content}
        if tool_calls:
            result_msg["tool_calls"] = tool_calls

        return ChatResult(
            messages=[result_msg],
            usage=None,
            metrics=self.last_metrics.to_dict(),
        )

    async def stream_chat(
            self,
            messages: List[ChatMessage],
            **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": self._build_prompt(messages),
            "stream": True,
        }
        if kwargs:
            payload.update(kwargs)

        t0 = time.perf_counter_ns()
        first_token_ns: int | None = None

        async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
        ) as client:
            async with client.stream("POST", "/api/generate", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    token = data.get("response", "")
                    if token:
                        if first_token_ns is None:
                            first_token_ns = time.perf_counter_ns()
                        yield token
                    if data.get("done"):
                        # Build metrics from final done packet
                        m = parse_ollama_metrics(data, t0)
                        # Override TTFT with wall-clock measurement if available
                        if first_token_ns is not None:
                            m.ttft_ms = (first_token_ns - t0) / 1e6
                        self.last_metrics = m
                        break


# Module-level default instance — reads settings at import time.
# cli.py and voice/cli_voice.py import this directly.
def _make_default_engine() -> OllamaEngine:
    try:
        from seraphim.settings import settings
        return OllamaEngine(
            model=settings.engine.model,
            base_url=settings.engine.base_url,
        )
    except Exception:
        return OllamaEngine()


engine = _make_default_engine()

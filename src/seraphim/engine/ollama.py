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
            options: dict | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.options = options or {}
        self.last_metrics: InferenceMetrics | None = None
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
        # Tighter timeout for streaming: 30 s per chunk — if no token arrives
        # within that window, something is hung and we should surface the error.
        self._stream_timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)

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
            r = await self._client.get("/api/tags", timeout=5.0)
            return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> List[str]:
        r = await self._client.get("/api/tags", timeout=10.0)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

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
            "keep_alive": "10m",
        }
        if self.options:
            payload["options"] = self.options
        if tools:
            payload["tools"] = tools
        # format kwarg (e.g. "json" or JSON schema dict) → structured output
        if "format" in kwargs:
            payload["format"] = kwargs.pop("format")
        if kwargs:
            payload.update(kwargs)

        t0 = time.perf_counter_ns()
        r = await self._client.post("/api/chat", json=payload)
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

        async with self._client.stream("POST", "/api/generate", json=payload, timeout=self._stream_timeout) as response:
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

    async def stream_chat_api(
            self,
            messages: List[ChatMessage],
            **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Stream via /api/chat — same message format as chat(), proper chat template."""
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
            "stream": True,
            "keep_alive": "10m",
        }
        if self.options:
            payload["options"] = self.options
        if kwargs:
            payload.update(kwargs)

        t0 = time.perf_counter_ns()
        first_token_ns: int | None = None

        async with self._client.stream("POST", "/api/chat", json=payload, timeout=self._stream_timeout) as response:
            if response.status_code >= 400:
                body = await response.aread()
                try:
                    detail = json.loads(body).get("error", body.decode("utf-8", errors="replace"))
                except Exception:
                    detail = body.decode("utf-8", errors="replace")
                raise httpx.HTTPStatusError(
                    f"Ollama {response.status_code}: {detail[:300]}",
                    request=response.request,
                    response=response,
                )
            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                token = (data.get("message") or {}).get("content") or ""
                if token:
                    if first_token_ns is None:
                        first_token_ns = time.perf_counter_ns()
                    yield token
                if data.get("done"):
                    m = parse_ollama_metrics(data, t0)
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
    except Exception as exc:
        import logging as _logging
        _logging.getLogger("seraphim.engine.ollama").warning(
            "Could not load engine settings (%s) — using defaults", exc
        )
        return OllamaEngine()


engine = _make_default_engine()

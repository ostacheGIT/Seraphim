"""OpenAI-compatible engine — works for OpenAI, Mistral, and any /v1/chat/completions API."""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from seraphim.engine.base import ChatMessage, ChatResult

logger = logging.getLogger(__name__)


class OpenAICompatEngine:
    """LLMEngine for any OpenAI-compatible REST API (OpenAI, Mistral, Together, …)."""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com",
        name: str = "OpenAI",
        engine_id: str = "openai",
        timeout: float = 120.0,
    ) -> None:
        self.id = engine_id
        self.name = name
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # Persistent client — reuses TLS connections across requests
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            limits=httpx.Limits(max_keepalive_connections=3, max_connections=5),
        )

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def chat(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ChatResult:
        payload: Dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
        resp = await self._client.post(
            f"{self.base_url}/v1/chat/completions",
            headers=self._headers(),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        content: str = data["choices"][0]["message"].get("content") or ""
        return {"messages": [{"role": "assistant", "content": content}]}

    async def stream_chat_api(
        self,
        messages: List[ChatMessage],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        async with self._client.stream(
            "POST",
            f"{self.base_url}/v1/chat/completions",
            headers=self._headers(),
            json=payload,
        ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        content = data["choices"][0]["delta"].get("content") or ""
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        pass

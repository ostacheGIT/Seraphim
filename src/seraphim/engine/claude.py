"""Anthropic Claude engine."""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

from seraphim.engine.base import ChatMessage, ChatResult

logger = logging.getLogger(__name__)


class ClaudeEngine:
    """LLMEngine for the Anthropic Claude API."""

    id = "claude"
    name = "Claude"
    _BASE_URL = "https://api.anthropic.com"
    _API_VERSION = "2023-06-01"

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        api_key: str = "",
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self._API_VERSION,
            "Content-Type": "application/json",
        }

    def _convert(self, messages: List[ChatMessage]) -> Tuple[Optional[str], List[Dict[str, str]]]:
        system: Optional[str] = None
        converted: List[Dict[str, str]] = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                system = content
            elif role in ("user", "assistant"):
                converted.append({"role": role, "content": content})
        return system, converted

    async def chat(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ChatResult:
        system, msgs = self._convert(messages)
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": msgs,
        }
        if system:
            payload["system"] = system
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self._BASE_URL}/v1/messages",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        content: str = (data.get("content") or [{}])[0].get("text", "")
        return {"messages": [{"role": "assistant", "content": content}]}

    async def stream_chat_api(
        self,
        messages: List[ChatMessage],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        system, msgs = self._convert(messages)
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": msgs,
            "stream": True,
        }
        if system:
            payload["system"] = system
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self._BASE_URL}/v1/messages",
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        data = json.loads(line[6:])
                        if data.get("type") == "content_block_delta":
                            text = data.get("delta", {}).get("text", "")
                            if text:
                                yield text
                    except (json.JSONDecodeError, KeyError):
                        pass

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from seraphim.engine.base import ChatMessage, ChatResult, LLMEngine


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
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": self._build_prompt(messages),
            "stream": False,
        }
        if kwargs:
            payload.update(kwargs)

        async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
        ) as client:
            r = await client.post("/api/generate", json=payload)
            r.raise_for_status()
            data = r.json()

        content = data.get("response", "") or ""
        return ChatResult(
            messages=[ChatMessage(role="assistant", content=content)],
            usage=None,
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
                        yield token
                    if data.get("done"):
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

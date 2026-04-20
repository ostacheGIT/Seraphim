"""
Seraphim Engine — local inference via Ollama.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from seraphim.settings import settings


class OllamaEngine:
    """Thin async wrapper around the Ollama REST API."""

    def __init__(self) -> None:
        self.base_url = settings.engine.base_url
        self.model = settings.engine.model
        self.temperature = settings.engine.temperature

    async def health_check(self) -> bool:
        """Return True if Ollama is reachable."""
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """Return available local models."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{self.base_url}/api/tags")
            r.raise_for_status()
            data = r.json()
            return [m["name"] for m in data.get("models", [])]

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> str:
        """Send a chat request and return the full response text."""
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": stream,
            "options": {
                "temperature": kwargs.get("temperature", self.temperature),
            },
        }

        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{self.base_url}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
            return data["message"]["content"]

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream a chat response token by token."""
        import json

        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": kwargs.get("temperature", self.temperature),
            },
        }

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST", f"{self.base_url}/api/chat", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.strip():
                        chunk = json.loads(line)
                        if token := chunk.get("message", {}).get("content", ""):
                            yield token


# Module-level engine singleton
engine = OllamaEngine()

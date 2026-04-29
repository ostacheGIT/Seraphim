from __future__ import annotations

from typing import Any, Dict, List, Optional

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
            model: str = "qwen2.5:3b",  # adapté à ton /api/tags
            base_url: str = "http://localhost:11434",
            timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def chat(
            self,
            messages: List[ChatMessage],
            tools: Optional[List[Dict[str, Any]]] = None,
            **kwargs: Any,
    ) -> ChatResult:
        """
        Simule un chat en concaténant les messages en un prompt unique.
        """

        prompt_parts: List[str] = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "") or ""
            if role == "system":
                prompt_parts.append(f"[SYSTEM] {content}\n")
            elif role == "user":
                prompt_parts.append(f"[USER] {content}\n")
            elif role == "assistant":
                prompt_parts.append(f"[ASSISTANT] {content}\n")
            else:
                prompt_parts.append(f"[{role.upper()}] {content}\n")

        prompt = "\n".join(prompt_parts).strip()

        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }

        # kwargs -> options Ollama (temperature, top_p, etc.) si besoin
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

        messages_out: List[ChatMessage] = [
            ChatMessage(
                role="assistant",
                content=content,
            )
        ]

        return ChatResult(
            messages=messages_out,
            usage=None,
        )
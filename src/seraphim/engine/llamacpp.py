from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from seraphim.engine.base import ChatMessage, ChatResult, LLMEngine


class LlamaCppEngine:
    """
    Implémentation LLMEngine pour un serveur HTTP LLaMA.cpp ou équivalent.

    On suppose un endpoint JSON de type /v1/chat/completions ou similaire.
    Adapte le path et le format du payload/réponse à ton serveur réel si besoin.
    """

    id = "llamacpp"
    name = "Llama.cpp HTTP"

    def __init__(
            self,
            model: str = "default",
            base_url: str = "http://localhost:8080",
            timeout: float = 60.0,
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
        Appelle le serveur HTTP local et normalise la réponse en ChatResult.
        """

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools

        payload.update(kwargs)

        async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
        ) as client:
            # Adapte la route suivant ton serveur (par ex. "/completion" ou autre).
            r = await client.post("/v1/chat/completions", json=payload)
            r.raise_for_status()
            data = r.json()

        messages_out: List[ChatMessage] = []
        for choice in data.get("choices", []):
            msg = choice.get("message") or {}
            role = msg.get("role", "assistant")
            content = msg.get("content", "") or ""
            messages_out.append(
                ChatMessage(
                    role=role,
                    content=content,
                )
            )

        return ChatResult(
            messages=messages_out,
            usage=data.get("usage"),
        )
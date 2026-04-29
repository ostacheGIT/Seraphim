from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, TypedDict


class ChatMessage(TypedDict, total=False):
    """
    Message de chat minimal compatible avec la plupart des backends.
    """
    role: str          # "user" | "assistant" | "system" | "tool"
    content: str
    name: str
    tool_call_id: str


class ChatResult(TypedDict, total=False):
    """
    Résultat normalisé d'un appel LLM.
    """
    messages: List[ChatMessage]
    usage: Optional[Dict[str, Any]]


class LLMEngine(Protocol):
    """
    Interface commune à tous les moteurs (Ollama, Llama.cpp, etc.).
    """

    id: str
    name: str

    async def chat(
            self,
            messages: List[ChatMessage],
            tools: Optional[List[Dict[str, Any]]] = None,
            **kwargs: Any,
    ) -> ChatResult:
        ...
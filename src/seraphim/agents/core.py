"""Base classes partagées — sans import circulaire."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from seraphim.engine import get_engine
from seraphim.engine.base import ChatMessage


@dataclass
class AgentContext:
    messages: list[ChatMessage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str) -> None:
        self.messages.append({"role": "assistant", "content": content})

    def add_system(self, content: str) -> None:
        # On garde ton comportement: le system en tête
        self.messages.insert(0, {"role": "system", "content": content})


class BaseAgent(ABC):
    name: str = "base"
    description: str = "Base agent"
    system_prompt: str = "You are Seraphim, a helpful personal AI assistant."

    def __init__(self, engine_id: Optional[str] = None) -> None:
        # On stocke seulement l'ID; le moteur réel est récupéré via get_engine
        self.engine_id = engine_id

    @property
    def engine(self):
        """
        Accès pratique au moteur LLM pour les agents concrets.

        Si engine_id est None, get_engine() renvoie le moteur par défaut
        (actuellement ollama_qwen3b).
        """
        return get_engine(self.engine_id)

    @abstractmethod
    async def run(self, query: str, context: AgentContext | None = None) -> str:
        ...

    def build_context(self, query: str, context: AgentContext | None = None) -> AgentContext:
        """
        Construit un AgentContext en ajoutant le system prompt s'il n'existe pas,
        puis le message utilisateur.
        """
        ctx = context or AgentContext()
        if not any(m.get("role") == "system" for m in ctx.messages):
            ctx.add_system(self.system_prompt)
        ctx.add_user(query)
        return ctx
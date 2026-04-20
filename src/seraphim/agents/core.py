"""Base classes partagées — sans import circulaire."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from seraphim.engine.ollama import OllamaEngine, engine as default_engine


@dataclass
class AgentContext:
    messages: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str) -> None:
        self.messages.append({"role": "assistant", "content": content})

    def add_system(self, content: str) -> None:
        self.messages.insert(0, {"role": "system", "content": content})


class BaseAgent(ABC):
    name: str = "base"
    description: str = "Base agent"
    system_prompt: str = "You are Seraphim, a helpful personal AI assistant."

    def __init__(self, engine: OllamaEngine = None) -> None:
        self.engine = engine or default_engine

    @abstractmethod
    async def run(self, query: str, context: "AgentContext" = None) -> str: ...

    def build_context(self, query: str, context: "AgentContext" = None) -> "AgentContext":
        ctx = context or AgentContext()
        if not any(m["role"] == "system" for m in ctx.messages):
            ctx.add_system(self.system_prompt)
        ctx.add_user(query)
        return ctx
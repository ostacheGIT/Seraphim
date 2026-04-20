"""
Seraphim Agents — base class and built-in chat agent.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from seraphim.engine.ollama import OllamaEngine, engine as default_engine


@dataclass
class AgentContext:
    """Carries the conversation history and metadata for an agent run."""

    messages: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str) -> None:
        self.messages.append({"role": "assistant", "content": content})

    def add_system(self, content: str) -> None:
        # System message always goes first
        self.messages.insert(0, {"role": "system", "content": content})


class BaseAgent(ABC):
    """Abstract base for all Seraphim agents."""

    name: str = "base"
    description: str = "Base agent"
    system_prompt: str = "You are Seraphim, a helpful personal AI assistant."

    def __init__(self, engine: OllamaEngine | None = None) -> None:
        self.engine = engine or default_engine

    @abstractmethod
    async def run(self, query: str, context: AgentContext | None = None) -> str:
        """Process a user query and return the response."""
        ...

    def _build_context(self, query: str, context: AgentContext | None) -> AgentContext:
        ctx = context or AgentContext()
        if not any(m["role"] == "system" for m in ctx.messages):
            ctx.add_system(self.system_prompt)
        ctx.add_user(query)
        return ctx


class ChatAgent(BaseAgent):
    """Simple conversational agent — the default Seraphim agent."""

    name = "chat"
    description = "Conversational agent for general questions and assistance"
    system_prompt = (
        "You are Seraphim, a helpful, concise, and friendly personal AI assistant. "
        "You run entirely on the user's local machine. "
        "Be direct, honest, and useful. Avoid unnecessary verbosity."
    )

    async def run(self, query: str, context: AgentContext | None = None) -> str:
        ctx = self._build_context(query, context)
        response = await self.engine.chat(ctx.messages)
        ctx.add_assistant(response)
        return response


class CoderAgent(BaseAgent):
    """Code-focused agent with deeper programming knowledge."""

    name = "coder"
    description = "Code assistant — debugging, refactoring, explanation, generation"
    system_prompt = (
        "You are Seraphim in coder mode. You are an expert software engineer. "
        "When writing code, prefer clarity over cleverness. "
        "Always explain your reasoning briefly. "
        "Use modern best practices for the language in question."
    )

    async def run(self, query: str, context: AgentContext | None = None) -> str:
        ctx = self._build_context(query, context)
        response = await self.engine.chat(ctx.messages)
        ctx.add_assistant(response)
        return response


class ResearcherAgent(BaseAgent):
    """Research agent for summarising documents and answering deep questions."""

    name = "researcher"
    description = "Research assistant — summarisation, Q&A on documents, analysis"
    system_prompt = (
        "You are Seraphim in researcher mode. "
        "You specialise in synthesising information, finding patterns, and producing "
        "well-structured, cited answers. Always structure your answers clearly."
    )

    async def run(self, query: str, context: AgentContext | None = None) -> str:
        ctx = self._build_context(query, context)
        response = await self.engine.chat(ctx.messages)
        ctx.add_assistant(response)
        return response


# Registry of available agents
AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
    "chat": ChatAgent,
    "coder": CoderAgent,
    "researcher": ResearcherAgent,
}


def get_agent(name: str) -> BaseAgent:
    """Instantiate an agent by name."""
    cls = AGENT_REGISTRY.get(name)
    if cls is None:
        available = ", ".join(AGENT_REGISTRY.keys())
        raise ValueError(f"Unknown agent '{name}'. Available: {available}")
    return cls()

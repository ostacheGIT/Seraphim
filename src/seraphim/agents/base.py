"""Agents built-in : chat, coder, researcher."""

from seraphim.agents.core import AgentContext, BaseAgent


class ChatAgent(BaseAgent):
    name = "chat"
    description = "Conversational agent for general questions and assistance"
    system_prompt = (
        "You are Seraphim, a helpful, concise, and friendly personal AI assistant. "
        "You run entirely on the user's local machine. Be direct, honest, and useful."
    )

    async def run(self, query: str, context: AgentContext = None) -> str:
        ctx = self.build_context(query, context)
        response = await self.engine.chat(ctx.messages)
        ctx.add_assistant(response)
        return response


class CoderAgent(BaseAgent):
    name = "coder"
    description = "Code assistant: debugging, refactoring, explanation, generation"
    system_prompt = (
        "You are Seraphim in coder mode. You are an expert software engineer. "
        "When writing code, prefer clarity over cleverness. "
        "Always explain your reasoning briefly. Use modern best practices."
    )

    async def run(self, query: str, context: AgentContext = None) -> str:
        ctx = self.build_context(query, context)
        response = await self.engine.chat(ctx.messages)
        ctx.add_assistant(response)
        return response


class ResearcherAgent(BaseAgent):
    name = "researcher"
    description = "Research assistant: summarisation, QA on documents, analysis"
    system_prompt = (
        "You are Seraphim in researcher mode. You specialise in synthesising information, "
        "finding patterns, and producing well-structured, cited answers."
    )

    async def run(self, query: str, context: AgentContext = None) -> str:
        ctx = self.build_context(query, context)
        response = await self.engine.chat(ctx.messages)
        ctx.add_assistant(response)
        return response


AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
    "chat": ChatAgent,
    "coder": CoderAgent,
    "researcher": ResearcherAgent,
}


def get_agent(name: str) -> BaseAgent:
    from seraphim.agents.react import ReactAgent
    AGENT_REGISTRY["react"] = ReactAgent

    cls = AGENT_REGISTRY.get(name)
    if cls is None:
        available = ", ".join(AGENT_REGISTRY.keys())
        raise ValueError(f"Unknown agent '{name}'. Available: {available}")
    return cls()
"""Agents built-in : chat, coder, researcher, react."""

import json
import re
from seraphim.agents.core import AgentContext, BaseAgent
from seraphim.skills.registry import discover_skills, get_all_tools, get_skill


discover_skills()


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


class ReActAgent(BaseAgent):
    """ReAct agent — thinks and acts using tools."""

    name = "react"
    description = "Agent ReAct — lit des fichiers, cherche sur le web, raisonne étape par étape"
    system_prompt = (
        "You are Seraphim in ReAct mode. You can use tools to answer the user.\n"
        "To call a tool, write EXACTLY this format (nothing before or after):\n"
        "ACTION: tool_name\n"
        "ARGS: {\"param\": \"value\"}\n\n"
        "Available tools:\n"
        "- read_file: read a file. Args: {\"path\": \"C:/Users/ostap/SERAPHIM/file.txt\"}\n"
        "- write_file: write a file. Args: {\"path\": \"...\", \"content\": \"...\"}\n"
        "- web_search: search the web. Args: {\"query\": \"...\"}\n\n"
        "IMPORTANT RULES:\n"
        "1. Always use forward slashes in paths (C:/Users/ostap/...), never backslashes.\n"
        "2. After receiving a RESULT, give your final answer using ONLY that result.\n"
        "3. Never invent or hallucinate file content. Only use what RESULT contains.\n"
        "4. The current working directory is: C:/Users/ostap/SERAPHIM"
    )

    async def run(self, query: str, context: AgentContext | None = None) -> str:
        import re, json
        ctx = self._build_context(query, context)

        for _ in range(8):
            response = await self.engine.chat(ctx.messages)

            action_match = re.search(r"ACTION:\s*(\w+)", response)
            args_match   = re.search(r"ARGS:\s*(\{.*?\})", response, re.DOTALL)

            if action_match:
                skill_name = action_match.group(1).strip()
                args = {}
                if args_match:
                    raw = args_match.group(1).replace("\\\\", "\\")
                    try:
                        args = json.loads(raw)
                    except json.JSONDecodeError:
                        pass

                # Normalise les slashes dans path
                if "path" in args:
                    args["path"] = args["path"].replace("/", "\\")

                try:
                    from seraphim.skills import SKILL_REGISTRY
                    skill = SKILL_REGISTRY[skill_name]()
                    result = await skill.run(**args)
                    tool_output = result.output if result.success else f"Error: {result.error}"
                except Exception as e:
                    tool_output = f"Skill error: {type(e).__name__}: {e}"

                ctx.messages.append({"role": "assistant", "content": response})
                ctx.messages.append({
                    "role": "user",
                    "content": (
                        f"RESULT of {skill_name}:\n```\n{tool_output}\n```\n\n"
                        "Now give your final answer using ONLY the above result. Do NOT invent anything."
                    )
                })

            else:
                ctx.add_assistant(response)
                return response

        return "I was unable to complete the task within the allowed steps."

AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
    "chat":       ChatAgent,
    "coder":      CoderAgent,
    "researcher": ResearcherAgent,
    "react":      ReActAgent,   # ← ajoute cette ligne
}


def get_agent(name: str) -> BaseAgent:
    cls = AGENT_REGISTRY.get(name)
    if cls is None:
        available = ", ".join(AGENT_REGISTRY.keys())
        raise ValueError(f"Unknown agent '{name}'. Available: {available}")
    return cls()
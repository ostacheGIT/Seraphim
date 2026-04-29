"""Agents built-in : chat, coder, researcher, react."""

import json
import re
from seraphim.agents.core import AgentContext, BaseAgent
from seraphim.skills.registry import discover_skills, get_all_tools, get_skill, SKILL_REGISTRY

discover_skills()

# ── Détection directe — bypass LLM total pour les commandes système ───────────
DIRECT_PATTERNS = [
    (re.compile(r"(?:ouvre|lance|démarre|open|start)\s+(\w+)", re.I),
     lambda m: ("open_app", {"app": m.group(1)})),
    (re.compile(r"(?:volume|son)\s+(?:à|a|=)?\s*(\d+)", re.I),
     lambda m: ("set_volume", {"level": int(m.group(1))})),
    (re.compile(r"(?:monte|augmente)\s+le\s+(?:volume|son)", re.I),
     lambda m: ("set_volume", {"level": 80})),
    (re.compile(r"(?:baisse|diminue)\s+le\s+(?:volume|son)", re.I),
     lambda m: ("set_volume", {"level": 20})),
    (re.compile(r"(?:coupe|mute)\s+le\s+(?:volume|son|micro)", re.I),
     lambda m: ("set_volume", {"mute": True})),
    (re.compile(r"(?:verrouille|lock)\s*(?:l.écran|le\s+pc|l.ordinateur)?", re.I),
     lambda m: ("system_control", {"action": "lock"})),
    (re.compile(r"(?:éteins?|shutdown|arrête)\s*(?:le\s+pc|l.ordi|l.ordinateur|windows)?", re.I),
     lambda m: ("system_control", {"action": "shutdown"})),
    (re.compile(r"(?:redémarre|restart|reboot)", re.I),
     lambda m: ("system_control", {"action": "restart"})),
    (re.compile(r"(?:veille|sleep|suspend)", re.I),
     lambda m: ("system_control", {"action": "sleep"})),
    (re.compile(r"(?:luminosité|brightness)\s+(?:à|a|=)?\s*(\d+)", re.I),
     lambda m: ("set_brightness", {"level": int(m.group(1))})),
    (re.compile(r"(?:cherche|recherche|google|trouve|search|quoi de neuf sur|news sur|infos sur)\s+(.+)", re.I),
     lambda m: ("web_search", {"query": m.group(1)})),
]


class ChatAgent(BaseAgent):
    name = "chat"
    description = "Conversational agent for general questions and assistance"
    system_prompt = (
        "You are Seraphim, a helpful, concise, and friendly personal AI assistant. "
        "You run entirely on the user's local machine. Be direct, honest, and useful."
    )

    async def run(self, query: str, context: AgentContext = None) -> str:
        # Bypass LLM pour les commandes système
        for pattern, builder in DIRECT_PATTERNS:
            m = pattern.search(query)
            if m:
                skill_name, kwargs = builder(m)
                skill = SKILL_REGISTRY.get(skill_name)
                if skill:
                    result = await skill.run(**kwargs)
                    return result.output

        # Conversation normale
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
        "- web_search: search the web and return top results. Args: {\"query\": \"...\", \"max_results\": 5}\n\n"
        "IMPORTANT RULES:\n"
        "1. Always use forward slashes in paths (C:/Users/ostap/...), never backslashes.\n"
        "2. After receiving a RESULT, give your final answer using ONLY that result.\n"
        "3. Never invent or hallucinate file content or web results. Only use what RESULT contains.\n"
        "4. The current working directory is: C:/Users/ostap/SERAPHIM\n"
        "5. For any question about current events, news, or real-time info, ALWAYS use web_search first."
    )

    async def run(self, query: str, context: AgentContext | None = None) -> str:
        # ── Détection directe — bypass LLM total ────────────────────────────
        for pattern, builder in DIRECT_PATTERNS:
            m = pattern.search(query)
            if m:
                skill_name, kwargs = builder(m)
                skill = SKILL_REGISTRY.get(skill_name)
                if not skill:
                    return f"Skill '{skill_name}' non trouvé."
                try:
                    result = await skill.run(**kwargs)
                    return result.output
                except Exception as e:
                    return f"Erreur : {e}"

        # ── ReAct loop standard pour tout le reste ───────────────────────────
        ctx = self.build_context(query, context)

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

                if "path" in args:
                    args["path"] = args["path"].replace("/", "\\")

                try:
                    skill = SKILL_REGISTRY[skill_name]
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
    "react":      ReActAgent,
}


def get_agent(name: str) -> BaseAgent:
    cls = AGENT_REGISTRY.get(name)
    if cls is None:
        available = ", ".join(AGENT_REGISTRY.keys())
        raise ValueError(f"Unknown agent '{name}'. Available: {available}")
    return cls()
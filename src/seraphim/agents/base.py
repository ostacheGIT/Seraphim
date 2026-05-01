"""Agents built-in : chat, coder, researcher, react."""

import json
import re
from seraphim.agents.core import AgentContext, BaseAgent
from seraphim.skills.registry import discover_skills, get_all_tools, get_skill, SKILL_REGISTRY

discover_skills()

# Matches queries that are purely math: "2 + 2", "sqrt(144)", "3**10", "combien font 5*7", etc.
_MATH_QUERY_RE = re.compile(
    r"^(?:"
    r"(?:combien\s+(?:font|fait|vaut|vale|égal(?:e)?|=)\s+)?"  # French prefix
    r"(?:calcule?\s+|compute\s+|evaluate\s+|eval\s+)?"         # optional verb
    r")?"
    r"(?P<expr>"
    r"[\d\s\+\-\*\/\%\(\)\.\,\^]+"                             # basic arithmetic chars
    r"|(?:sqrt|sin|cos|tan|log|abs|round|min|max|pi|e)\b.*"    # math function calls
    r")"
    r"[?!.\s]*$",
    re.I,
)


def _extract_math_expr(query: str) -> str | None:
    """Return a calculator-friendly expression if the query is pure math, else None."""
    q = query.strip()
    m = _MATH_QUERY_RE.match(q)
    if not m:
        return None
    expr = m.group("expr").strip().replace("^", "**").replace(",", ".")
    # Require at least one digit or named constant to avoid matching words
    if not re.search(r"[\d]|pi\b|e\b", expr):
        return None
    # Reject if it looks like a sentence (contains alpha words > 2 chars that aren't math fns)
    _MATH_FNS = {"sqrt", "sin", "cos", "tan", "log", "abs", "round", "min", "max", "pi"}
    words = re.findall(r"[a-zA-Z]{3,}", expr)
    if any(w.lower() not in _MATH_FNS for w in words):
        return None
    return expr


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
        # Bypass LLM — math expressions
        expr = _extract_math_expr(query)
        if expr:
            skill = SKILL_REGISTRY.get("calculator")
            if skill:
                result = await skill.run(expression=expr)
                if result.success:
                    return result.output
                # fall through to LLM if expression was invalid

        # Bypass LLM — system commands
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
        response = await self._chat(ctx.messages)
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
        response = await self._chat(ctx.messages)
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
        response = await self._chat(ctx.messages)
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
        "- web_search: search the web. Args: {\"query\": \"...\", \"max_results\": 5}\n"
        "- think: reason step-by-step before answering. Args: {\"thought\": \"...\"}\n"
        "- calculator: evaluate math expressions safely. Args: {\"expression\": \"2**10 + sqrt(144)\"}\n"
        "- code_interpreter: run Python code in a subprocess. Args: {\"code\": \"print(1+1)\", \"timeout\": 15}\n"
        "- repl: persistent Python REPL (variables survive between calls). Args: {\"code\": \"x = 42\", \"reset\": false}\n"
        "- http_request: make HTTP requests. Args: {\"url\": \"https://...\", \"method\": \"GET\"}\n\n"
        "IMPORTANT RULES:\n"
        "1. Always use forward slashes in paths (C:/Users/ostap/...), never backslashes.\n"
        "2. After receiving a RESULT, give your final answer using ONLY that result.\n"
        "3. Never invent or hallucinate file content or web results. Only use what RESULT contains.\n"
        "4. The current working directory is: C:/Users/ostap/SERAPHIM\n"
        "5. For any question about current events, news, or real-time info, ALWAYS use web_search first.\n"
        "6. Use think before complex multi-step reasoning to plan your approach.\n"
        "7. Use repl for stateful computation across multiple steps; use code_interpreter for one-shot scripts."
    )

    async def run(self, query: str, context: AgentContext | None = None) -> str:
        # ── Détection directe — bypass LLM total ────────────────────────────
        expr = _extract_math_expr(query)
        if expr:
            skill = SKILL_REGISTRY.get("calculator")
            if skill:
                try:
                    result = await skill.run(expression=expr)
                    return result.output
                except Exception as e:
                    return f"Calculator error: {e}"

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
            response = await self._chat(ctx.messages)

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

class SkillAgent(BaseAgent):
    name = "skill"
    description = "Exécute un skill Hermes installé"

    def __init__(self, skill_name: str):
        super().__init__()
        self.skill_name = skill_name
        self.system_prompt = self._load_skill_prompt()

    def _load_skill_prompt(self) -> str:
        from pathlib import Path
        skills_root = Path("~/.seraphim/skills").expanduser()
        for skill_md in skills_root.rglob(f"{self.skill_name}/SKILL.md"):
            return skill_md.read_text(encoding="utf-8")
        raise FileNotFoundError(
            f"Skill '{self.skill_name}' non trouvé. "
            f"Installez-le : seraphim skill import {self.skill_name}"
        )

    async def run(self, query: str, context: AgentContext = None) -> str:
        ctx = self.build_context(query, context)
        result = await self.engine.chat(ctx.messages)
        msgs = result.get("messages", [])
        response = msgs[-1].get("content", "") if msgs else ""
        ctx.add_assistant(response)
        return response

AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
    "chat":       ChatAgent,
    "coder":      CoderAgent,
    "researcher": ResearcherAgent,
    "react":      ReActAgent,
    "skill":      SkillAgent,
}

def get_agent(name: str) -> BaseAgent:
    if name.startswith("skill:"):
        skill_name = name.split(":", 1)[1]
        return SkillAgent(skill_name)
    cls = AGENT_REGISTRY.get(name)
    if cls is None:
        available = ", ".join(AGENT_REGISTRY.keys())
        raise ValueError(f"Unknown agent '{name}'. Available: {available}")
    return cls()
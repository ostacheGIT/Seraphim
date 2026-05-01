"""Agents built-in : chat, coder, researcher, react."""

import json
import re
from pathlib import Path
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
    (re.compile(r"(?:liste|list|affiche|montre|show)\s+(?:les\s+)?(?:fichiers?|dossiers?|files?)\s+(?:dans|in|de|of|du|à|at)\s+(.+)", re.I),
     lambda m: ("list_files", {"path": m.group(1).strip()})),
    (re.compile(r"(?:lis|lire|read|ouvre|open)\s+(?:le\s+fichier\s+|fichier\s+)?['\"]?([\w/\\.~-]+\.\w+)['\"]?", re.I),
     lambda m: ("read_file", {"path": m.group(1).strip()})),
]


_IDENTITY_BLOCK = (
    "\n\n=== IDENTITY (ABSOLUTE, NON-NEGOTIABLE) ===\n"
    "Your name is Seraphim. You are a personal AI assistant running on this user's local machine.\n"
    "You are NOT Qwen. You are NOT an Alibaba product. You are NOT ChatGPT. You are NOT Claude.\n"
    "If asked who you are: always answer 'I am Seraphim'.\n"
    "NEVER say 'as an AI I have no internet access' — you DO have web search via your tools.\n"
    "NEVER say 'I cannot access external resources' — you CAN search the web.\n"
    "When you searched the web in a previous message, say so clearly: 'I found this by searching the web.'\n"
    "=== END IDENTITY ==="
)


class ChatAgent(BaseAgent):
    name = "chat"
    description = "Conversational agent for general questions and assistance"
    system_prompt = (
        "You are Seraphim, a helpful, concise, and friendly personal AI assistant. "
        "You run entirely on the user's local machine. Be direct, honest, and useful. "
        + _IDENTITY_BLOCK
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
                    return result.output if result.output else (result.error or "(no output)")

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
        "Always explain your reasoning briefly. Use modern best practices. "
        + _IDENTITY_BLOCK
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
        "finding patterns, and producing well-structured, cited answers. "
        + _IDENTITY_BLOCK
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

    @property
    def system_prompt(self) -> str:
        cwd = Path.cwd().as_posix()
        return (
            "You are Seraphim in ReAct mode. You can use tools to answer the user.\n"
            "To call a tool, write EXACTLY this format (nothing before or after):\n"
            "ACTION: tool_name\n"
            "ARGS: {\"param\": \"value\"}\n\n"
            "Available tools:\n"
            f"- read_file: read a file. Args: {{\"path\": \"{cwd}/file.txt\"}}\n"
            "- write_file: write a file. Args: {\"path\": \"...\", \"content\": \"...\"}\n"
            "- web_search: search the web. Args: {\"query\": \"...\", \"max_results\": 5}\n"
            "- think: reason step-by-step before answering. Args: {\"thought\": \"...\"}\n"
            "- calculator: evaluate math expressions safely. Args: {\"expression\": \"2**10 + sqrt(144)\"}\n"
            "- code_interpreter: run Python code in a subprocess. Args: {\"code\": \"print(1+1)\", \"timeout\": 15}\n"
            "- repl: persistent Python REPL (variables survive between calls). Args: {\"code\": \"x = 42\", \"reset\": false}\n"
            "- http_request: make HTTP requests. Args: {\"url\": \"https://...\", \"method\": \"GET\"}\n\n"
            "IMPORTANT RULES:\n"
            "1. Always use forward slashes in paths, never backslashes.\n"
            "2. After receiving a RESULT, give your final answer using ONLY that result.\n"
            "3. Never invent or hallucinate file content or web results. Only use what RESULT contains.\n"
            f"4. The current working directory is: {cwd}\n"
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
                    return result.output if result.output else (result.error or "(no output)")
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
                        f"Tool result ({skill_name}):\n{tool_output}\n\n"
                        "Summarize the above result in plain language. "
                        "Do NOT repeat the raw output. Do NOT say ACTION or ARGS. "
                        "Just give a short, clear answer."
                    )
                })

            else:
                ctx.add_assistant(response)
                return response

        return "I was unable to complete the task within the allowed steps."

class BuiltinSkillAgent(BaseAgent):
    """Routes built-in SKILL_REGISTRY skills directly without YAML file lookup."""

    name = "builtin_skill"
    description = "Direct built-in skill invocation"

    def __init__(self, skill_name: str) -> None:
        super().__init__()
        self.skill_name = skill_name
        self.system_prompt = f"You are Seraphim. Use the {skill_name} tool to help the user."

    async def run(self, query: str, context: AgentContext | None = None) -> str:
        skill = SKILL_REGISTRY.get(self.skill_name)
        if skill is None:
            ctx = self.build_context(query, context)
            return await self._chat(ctx.messages)

        try:
            if self.skill_name == "web_search":
                result = await skill.run(query=query)
                if not result.success:
                    return result.output
                ctx = self.build_context(
                    f"You just searched the web for: '{query}'\n\n"
                    f"Search results:\n{result.output}\n\n"
                    "Synthesize these results into a concise, helpful answer in the user's language. "
                    "Start your answer by briefly mentioning that you searched the web (e.g. 'I searched the web and found...'). "
                    "Cite the sources. Do not say you have no internet access — you just proved you do.",
                    context,
                )
                return await self._chat(ctx.messages)

            elif self.skill_name == "calculator":
                expr = _extract_math_expr(query) or query
                result = await skill.run(expression=expr)

            elif self.skill_name == "think":
                think_result = await skill.run(thought=query)
                ctx = self.build_context(
                    f"Reasoning:\n{think_result.output}\n\nNow answer concisely: {query}",
                    context,
                )
                return await self._chat(ctx.messages)

            elif self.skill_name == "code_interpreter":
                code_ctx = self.build_context(
                    "Generate Python code to solve the request below. "
                    "Return ONLY raw Python code, no markdown fences, no explanation:\n" + query,
                    context,
                )
                code = await self._chat(code_ctx.messages)
                import re as _re
                m = _re.search(r"```python\n(.*?)\n```", code, _re.DOTALL)
                if m:
                    code = m.group(1)
                result = await skill.run(code=code)

            else:
                try:
                    result = await skill.run(query=query)
                except TypeError:
                    result = await skill.run(thought=query)

        except Exception as e:
            return f"Skill error ({self.skill_name}): {e}"

        return result.output if result.success else f"Error: {result.error}"


class SkillAgent(BaseAgent):
    name = "skill"
    description = "Exécute un skill Hermes installé (YAML externe)"

    def __init__(self, skill_name: str):
        super().__init__()
        self.skill_name = skill_name
        self.system_prompt = self._load_skill_prompt()

    def _load_skill_prompt(self) -> str:
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
        if not SKILL_REGISTRY:
            discover_skills()
        if skill_name in SKILL_REGISTRY:
            return BuiltinSkillAgent(skill_name)
        return SkillAgent(skill_name)
    cls = AGENT_REGISTRY.get(name)
    if cls is None:
        available = ", ".join(AGENT_REGISTRY.keys())
        raise ValueError(f"Unknown agent '{name}'. Available: {available}")
    return cls()
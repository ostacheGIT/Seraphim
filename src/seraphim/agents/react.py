"""ReAct agent — Thought / Action / Observation loop."""

import ast
import asyncio
import math
import re
from pathlib import Path

from seraphim.agents.core import AgentContext, BaseAgent
from seraphim.skills.registry import discover_skills, SKILL_REGISTRY

discover_skills()

TOOLS_DESCRIPTION = """- calculate: Evaluate a math expression. Example: calculate("3.14159 * 7 ** 2")
- read_file: Read the CONTENT of a local file (txt, py, json...). Example: read_file("~/notes.txt")
- write_file: Write to a file. Example: write_file("~/out.txt", "content")
- list_files: List files in a directory. Example: list_files("~/Seraphim/src")
- open_app: Launch/open a desktop application (NOT a file). Use this when the user says "ouvre", "lance", "démarre", "open" followed by an app name like spotify, chrome, notepad, discord, vscode, explorer. Example: open_app("notepad"), open_app("spotify")
- set_volume: Set system volume (0-100). Use when user says "volume", "son", "baisse", "monte". Example: set_volume(50)
- system_control: Control system power state. Use when user says "verrouille", "éteins", "redémarre", "mets en veille". Example: system_control("lock") — actions: lock, shutdown, restart, sleep
- set_brightness: Set screen brightness (0-100). Example: set_brightness(80)"""

REACT_SYSTEM_PROMPT = f"""You are Seraphim, an expert reasoning agent. You solve problems methodically using the ReAct framework.

Available tools:
{TOOLS_DESCRIPTION}

## Strict format — follow exactly:

Thought: Analyze the problem carefully. What do I know? What do I need to find out? What is my plan?
Action: <tool_name>(<arguments>)
Observation: <tool result>
Thought: What does this result tell me? Is it correct? Do I need another step?
Action: ...
Observation: ...
Thought: I have enough information to answer precisely.
Answer: <clear, complete, well-formatted final answer>

## Rules:
- NEVER skip the Thought step — always reason before acting.
- NEVER invent an Observation — it comes from real tool execution.
- If the result seems wrong, recalculate or try a different approach.
- Verify your answer before writing it.
- Be precise and concise in the Answer.
- Max 8 iterations.
- If no tool is needed, reason briefly then write Answer directly.
- read_file and write_file are ONLY for actual file paths (e.g. "notes.txt", "~/doc.py"). NEVER use them for app names.
- open_app is for launching desktop software. "notepad", "spotify", "chrome", "discord", "vscode", "explorer" are app names, NOT files.

## Examples (few-shot):

User: ouvre notepad
Thought: The user wants to open the Notepad application. I should use open_app.
Action: open_app("notepad")
Observation: ✓ notepad ouvert.
Thought: Done.
Answer: Notepad est ouvert.

User: lance spotify
Thought: The user wants to open Spotify. I should use open_app.
Action: open_app("spotify")
Observation: ✓ spotify ouvert.
Thought: Done.
Answer: Spotify est lancé.

User: règle le volume à 40
Thought: The user wants to set volume to 40. I should use set_volume.
Action: set_volume(40)
Observation: 🔊 Volume réglé à 40%.
Thought: Done.
Answer: Volume réglé à 40%.

User: verrouille l'écran
Thought: The user wants to lock the screen. I should use system_control with action "lock".
Action: system_control("lock")
Observation: ✓ Système verrouillé.
Thought: Done.
Answer: Écran verrouillé.
"""

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
]


def _calculate(expr: str) -> str:
    try:
        allowed = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
        return str(eval(expr, {"__builtins__": {}}, allowed))
    except Exception as e:
        return f"Error: {e}"


def _read_file(path: str) -> str:
    try:
        p = Path(path).expanduser()
        if not p.exists():
            return f"Error: file '{path}' not found."
        content = p.read_text()
        return content[:50_000] + "\n[truncated]" if len(content) > 50_000 else content
    except Exception as e:
        return f"Error: {e}"


def _write_file(path: str, content: str) -> str:
    try:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Written {len(content)} chars to {path}"
    except Exception as e:
        return f"Error: {e}"


def _list_files(path: str) -> str:
    try:
        p = Path(path).expanduser()
        if not p.exists():
            return f"Error: directory '{path}' not found."
        return "\n".join(sorted(str(f.relative_to(p)) for f in p.iterdir())) or "(empty)"
    except Exception as e:
        return f"Error: {e}"


def _run_skill(name: str, **kwargs) -> str:
    skill = SKILL_REGISTRY.get(name)
    if not skill:
        return f"Error: skill '{name}' not found."
    try:
        result = asyncio.run(skill.run(**kwargs))
        return result.output
    except Exception as e:
        return f"Error: {e}"


def execute_tool(name: str, args: list) -> str:
    if name == "calculate":
        return _calculate(args[0]) if args else "Error: missing expression"
    if name == "read_file":
        return _read_file(args[0]) if args else "Error: missing path"
    if name == "write_file":
        return _write_file(args[0], args[1]) if len(args) >= 2 else "Error: missing args"
    if name == "list_files":
        return _list_files(args[0]) if args else "Error: missing path"
    if name == "open_app":
        return _run_skill("open_app", app=args[0]) if args else "Error: missing app name"
    if name == "set_volume":
        return _run_skill("set_volume", level=int(args[0])) if args else "Error: missing level"
    if name == "system_control":
        return _run_skill("system_control", action=args[0]) if args else "Error: missing action"
    if name == "set_brightness":
        return _run_skill("set_brightness", level=int(args[0])) if args else "Error: missing level"
    return f"Error: unknown tool '{name}'"


def parse_action(text: str):
    m = re.search(r"Action:\s*(\w+)\((.*)?\)\s*$", text, re.MULTILINE | re.DOTALL)
    if not m:
        return None
    tool_name = m.group(1).strip()
    raw_args = m.group(2).strip()
    try:
        args = list(ast.literal_eval(f"({raw_args},)"))
    except Exception:
        args = [raw_args] if raw_args else []
    return tool_name, args


def parse_answer(text: str):
    m = re.search(r"Answer:\s*(.+)", text, re.DOTALL)
    return m.group(1).strip() if m else None


class ReactAgent(BaseAgent):
    name = "react"
    description = "ReAct agent — reasons step by step and uses tools"
    system_prompt = REACT_SYSTEM_PROMPT

    async def run(self, query: str, context: AgentContext = None) -> str:
        # ── Détection directe — bypass LLM total ────────────────────────────
        for pattern, builder in DIRECT_PATTERNS:
            m = pattern.search(query)
            if m:
                skill_name, kwargs = builder(m)
                return _run_skill(skill_name, **kwargs)

        # ── ReAct loop standard pour tout le reste ───────────────────────────
        ctx = AgentContext()
        ctx.add_system(self.system_prompt)

        ctx.add_user(f"Before solving, briefly plan your approach in 2-3 steps:\nQuestion: {query}")
        plan = await self.engine.chat(ctx.messages)
        ctx.add_assistant(plan)

        ctx.add_user("Good. Now solve it step by step following your plan.")
        full_trace = ""

        for _ in range(8):
            response = await self.engine.chat(ctx.messages)
            full_trace += response + "\n"

            answer = parse_answer(response)
            if answer:
                return answer

            action = parse_action(response)
            if action:
                tool_name, args = action
                observation = execute_tool(tool_name, args)
                ctx.add_assistant(f"{response}\nObservation: {observation}")
                ctx.add_user("Continue.")
            else:
                return response

        return f"[ReAct] Max iterations reached.\n\n{full_trace}"

    async def _verify(self, query: str, answer: str, trace: str) -> str:
        verification_prompt = [
            {"role": "system", "content": "You are a careful reviewer. Check if the answer is correct, complete, and well-formatted."},
            {"role": "user", "content": f"Original question: {query}\n\nReasoning trace:\n{trace}\n\nProposed answer: {answer}\n\nIs this answer correct and complete? If yes, return it as-is. If not, correct it. Return only the final answer, nothing else."},
        ]
        return await self.engine.chat(verification_prompt)
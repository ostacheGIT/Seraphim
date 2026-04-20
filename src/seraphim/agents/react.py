"""ReAct agent — Thought / Action / Observation loop."""

import ast
import math
import re
from pathlib import Path

from seraphim.agents.core import AgentContext, BaseAgent


TOOLS_DESCRIPTION = """- calculate: Evaluate a math expression. Example: calculate("3.14159 * 7 ** 2")
- read_file: Read a local file. Example: read_file("~/notes.txt")
- write_file: Write to a file. Example: write_file("~/out.txt", "content")
- list_files: List files in a directory. Example: list_files("~/Seraphim/src")"""

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
"""


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


def execute_tool(name: str, args: list) -> str:
    if name == "calculate":
        return _calculate(args[0]) if args else "Error: missing expression"
    if name == "read_file":
        return _read_file(args[0]) if args else "Error: missing path"
    if name == "write_file":
        return _write_file(args[0], args[1]) if len(args) >= 2 else "Error: missing args"
    if name == "list_files":
        return _list_files(args[0]) if args else "Error: missing path"
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
        ctx = AgentContext()
        ctx.add_system(self.system_prompt)
        
        # Étape 0 — planification avant d'agir
        ctx.add_user(f"Before solving, briefly plan your approach in 2-3 steps:\nQuestion: {query}")
        plan = await self.engine.chat(ctx.messages)
        ctx.add_assistant(plan)
        
        # Étape 1 — résolution avec le plan en tête
        ctx.add_user(f"Good. Now solve it step by step following your plan.")
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
        """Demande à l'agent de vérifier sa propre réponse."""
        verification_prompt = [
            {"role": "system", "content": "You are a careful reviewer. Check if the answer is correct, complete, and well-formatted."},
            {"role": "user", "content": f"Original question: {query}\n\nReasoning trace:\n{trace}\n\nProposed answer: {answer}\n\nIs this answer correct and complete? If yes, return it as-is. If not, correct it. Return only the final answer, nothing else."},
        ]
        return await self.engine.chat(verification_prompt)
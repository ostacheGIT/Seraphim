"""Auto-correction utilities: detect issues in LLM responses and attempt to fix them."""
from __future__ import annotations

import ast
import logging
import re

logger = logging.getLogger(__name__)


_TRACEBACK_RE = re.compile(
    r"Traceback \(most recent call last\)|"
    r"(?:SyntaxError|NameError|TypeError|ValueError|AttributeError|"
    r"ImportError|ModuleNotFoundError|KeyError|IndexError|RuntimeError|"
    r"ZeroDivisionError|FileNotFoundError|PermissionError):\s",
)

_USER_CORRECTION_RE = re.compile(
    r"(?:"
    r"c'?est\s+(?:faux|pas\s+(?:correct|juste|vrai)|incorrect|une\s+erreur)|"
    r"tu\s+(?:te?\s+trompé|as\s+tort|fais\s+(?:une\s+)?erreur)|"
    r"(?:ce\s+n'est|c'est)\s+pas\s+(?:correct|juste|vrai|exact)|"
    r"(?:revise|révise|corrige|recalcule|vérifie)\s+(?:(?:ta|ton|la|le)\s+)?(?:réponse|calcul|code)|"
    r"(?:that'?s?\s+(?:wrong|incorrect|not\s+right))|"
    r"(?:you'?re?\s+(?:wrong|incorrect|mistaken))|"
    r"(?:wrong|incorrect)\s+(?:answer|result)|"
    r"(?:mauvaise|fausse)\s+(?:réponse|information|info)"
    r")",
    re.I,
)


def extract_python_blocks(text: str) -> list[str]:
    return re.findall(r"```python\n(.*?)```", text, re.DOTALL)


def check_python_syntax(code: str) -> str | None:
    """Returns an error description if code has a syntax error, else None."""
    try:
        ast.parse(code)
        return None
    except SyntaxError as e:
        return f"SyntaxError at line {e.lineno}: {e.msg}"


def has_traceback(text: str) -> bool:
    """True if text contains a Python traceback or exception."""
    return bool(_TRACEBACK_RE.search(text))


def is_user_correction(text: str) -> bool:
    """True if the user is signalling the previous response was wrong."""
    return bool(_USER_CORRECTION_RE.search(text.strip()))


async def self_correct_code(
    agent,
    query: str,
    response: str,
    context,
    max_rounds: int = 2,
) -> str:
    """
    If `response` contains Python blocks with syntax errors, ask the agent to
    fix them. Returns the corrected response, or the original if clean.
    """
    blocks = extract_python_blocks(response)
    syntax_errors = [(b, e) for b in blocks if (e := check_python_syntax(b))]
    if not syntax_errors:
        return response

    error_summary = "\n".join(f"- {err}" for _, err in syntax_errors)
    logger.info("[self_correct] syntax errors in %s response: %s", agent.name, error_summary)

    context.add_user(
        f"The code you wrote has syntax error(s):\n{error_summary}\n\n"
        "Please rewrite and fix the code. Keep the same format (FILENAME: + ```python block)."
    )

    for _ in range(max_rounds):
        corrected = await agent._chat(context.messages)
        context.add_assistant(corrected)
        remaining = [check_python_syntax(b) for b in extract_python_blocks(corrected)]
        if not any(remaining):
            return corrected
        remaining_errors = "; ".join(e for e in remaining if e)
        context.add_user(f"Still has syntax errors: {remaining_errors}. Fix them.")

    return corrected

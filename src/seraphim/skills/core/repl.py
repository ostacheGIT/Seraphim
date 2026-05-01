import io
import sys
import traceback

from seraphim.skills.base import BaseSkill, SkillResult

_MAX_OUTPUT = 4000


class ReplSkill(BaseSkill):
    """Persistent Python REPL — variables and imports survive between calls."""

    name = "repl"
    description = (
        "Persistent Python REPL. Variables, imports, and functions defined in one call "
        "are available in the next. Use reset=true to clear the state."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute",
            },
            "reset": {
                "type": "boolean",
                "description": "Clear the REPL namespace before executing (default: false)",
                "default": False,
            },
        },
        "required": ["code"],
    }

    def __init__(self) -> None:
        self._namespace: dict = {}

    async def run(self, code: str, reset: bool = False, **kwargs) -> SkillResult:
        if reset:
            self._namespace = {}

        buf = io.StringIO()
        old_stdout, old_stderr = sys.stdout, sys.stderr
        try:
            sys.stdout = buf
            sys.stderr = buf
            exec(code, self._namespace)
            output = buf.getvalue()[:_MAX_OUTPUT]
            return SkillResult(
                success=True,
                output=output.strip() or "(executed — no output)",
            )
        except Exception:
            err = traceback.format_exc()
            captured = buf.getvalue()
            return SkillResult(
                success=False,
                output=captured[:_MAX_OUTPUT] if captured else "",
                error=err[:_MAX_OUTPUT],
            )
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

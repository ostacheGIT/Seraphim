import contextlib
import io
import re
import traceback

from seraphim.skills.base import BaseSkill, SkillResult

_MAX_OUTPUT = 4000

_DANGEROUS_PATTERNS = re.compile(
    r"(?:os|subprocess)\s*\.\s*(?:system|popen|Popen|getoutput|call|run)\s*\("
    r"|shutil\s*\.\s*rmtree\s*\("
    r"|os\s*\.\s*(?:remove|unlink|rmdir|removedirs)\s*\("
    r"|__import__\s*\(\s*['\"](?:os|subprocess|shutil|ctypes)['\"]"
    r"|importlib\s*(?:\.\s*\w+)?\s*\.\s*import_module\s*\(\s*['\"](?:os|subprocess|shutil|ctypes)['\"]"
    r"|ctypes\s*\.\s*(?:cdll|windll|CDLL|WinDLL|LibraryLoader)"
)


def _check_dangerous(code: str) -> str | None:
    m = _DANGEROUS_PATTERNS.search(code)
    if m:
        return f"Blocked: dangerous pattern detected near '{m.group()[:40]}'"
    return None


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
        danger = _check_dangerous(code)
        if danger:
            return SkillResult(success=False, output="", error=danger)

        if reset:
            self._namespace = {}

        buf = io.StringIO()
        try:
            # redirect_stdout/stderr is context-local — safer than sys.stdout = buf
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                exec(code, self._namespace)  # noqa: S102
            output = buf.getvalue()[:_MAX_OUTPUT]
            return SkillResult(
                success=True,
                output=output.strip() or "(executed — no output)",
            )
        except SystemExit as exc:
            captured = buf.getvalue()
            return SkillResult(
                success=False,
                output=captured[:_MAX_OUTPUT] if captured else "",
                error=f"sys.exit({exc.code}) called — blocked to protect the server",
            )
        except KeyboardInterrupt:
            captured = buf.getvalue()
            return SkillResult(
                success=False,
                output=captured[:_MAX_OUTPUT] if captured else "",
                error="KeyboardInterrupt raised in code",
            )
        except Exception:
            err = traceback.format_exc()
            captured = buf.getvalue()
            return SkillResult(
                success=False,
                output=captured[:_MAX_OUTPUT] if captured else "",
                error=err[:_MAX_OUTPUT],
            )

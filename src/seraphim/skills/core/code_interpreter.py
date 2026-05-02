import os
import re
import subprocess
import sys
import tempfile

from seraphim.skills.base import BaseSkill, SkillResult

_MAX_OUTPUT = 4000

_DANGEROUS_PATTERNS = re.compile(
    r"(?:os|subprocess)\s*\.\s*(?:system|popen|Popen|getoutput|call|run)\s*\("
    r"|shutil\s*\.\s*rmtree\s*\("
    r"|os\s*\.\s*(?:remove|unlink|rmdir|removedirs)\s*\("
    r"|__import__\s*\(\s*['\"](?:os|subprocess|shutil)['\"]"
)


def _check_dangerous(code: str) -> str | None:
    m = _DANGEROUS_PATTERNS.search(code)
    if m:
        return f"Blocked: dangerous pattern detected near '{m.group()[:40]}'"
    return None


class CodeInterpreterSkill(BaseSkill):
    name = "code_interpreter"
    description = (
        "Execute Python code in an isolated subprocess and return stdout/stderr. "
        "Use for one-shot scripts, data processing, or verifying logic."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute",
            },
            "timeout": {
                "type": "integer",
                "description": "Max execution time in seconds (default: 15)",
                "default": 15,
            },
        },
        "required": ["code"],
    }

    async def run(self, code: str, timeout: int = 15, **kwargs) -> SkillResult:
        danger = _check_dangerous(code)
        if danger:
            return SkillResult(success=False, output="", error=danger)

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as f:
                f.write(code)
                tmp_path = f.name

            proc = subprocess.run(
                [sys.executable, tmp_path],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout = (proc.stdout or "")[:_MAX_OUTPUT]
            stderr = (proc.stderr or "")[:_MAX_OUTPUT]
            output = stdout
            if stderr:
                output += f"\n--- stderr ---\n{stderr}"
            return SkillResult(
                success=proc.returncode == 0,
                output=output.strip() or "(no output)",
                error=stderr if proc.returncode != 0 else "",
            )
        except subprocess.TimeoutExpired:
            return SkillResult(
                success=False, output="",
                error=f"Execution timed out after {timeout}s",
            )
        except Exception as e:
            return SkillResult(success=False, output="", error=str(e))
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

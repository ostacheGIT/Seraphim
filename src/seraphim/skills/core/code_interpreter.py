import subprocess
import sys

from seraphim.skills.base import BaseSkill, SkillResult

_MAX_OUTPUT = 4000


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
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout = proc.stdout[:_MAX_OUTPUT]
            stderr = proc.stderr[:_MAX_OUTPUT]
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

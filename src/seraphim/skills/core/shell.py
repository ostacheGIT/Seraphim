"""ShellSkill — exécute des commandes shell réelles (cmd/bash).

Utilisé par les skills externes qui ont `allowed-tools: Bash(...)` dans leur SKILL.md.
"""

import os
import subprocess
import sys
from pathlib import Path
from seraphim.skills.base import BaseSkill, SkillResult

_MAX_OUTPUT = 6000


class ShellSkill(BaseSkill):
    name = "shell"
    description = (
        "Execute a shell command and return stdout/stderr. "
        "Use for CLI tools: agent-browser, npm, git, python scripts, etc."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to run (e.g. 'agent-browser screenshot https://...')",
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds to wait (default: 60)",
                "default": 60,
            },
        },
        "required": ["command"],
    }

    async def run(self, command: str, timeout: int = 60, **kwargs) -> SkillResult:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,  # bytes mode — decode manually
                timeout=timeout,
            )
            enc = "utf-8"
            stdout = proc.stdout.decode(enc, errors="replace")[:_MAX_OUTPUT] if proc.stdout else ""
            stderr = proc.stderr.decode(enc, errors="replace")[:2000] if proc.stderr else ""
            output = stdout
            if stderr:
                output += f"\n--- stderr ---\n{stderr}"
            return SkillResult(
                success=proc.returncode == 0,
                output=output.strip() or "(no output)",
                error=stderr if proc.returncode != 0 else "",
            )
        except subprocess.TimeoutExpired:
            return SkillResult(success=False, output="", error=f"Timeout after {timeout}s")
        except Exception as e:
            return SkillResult(success=False, output="", error=str(e))

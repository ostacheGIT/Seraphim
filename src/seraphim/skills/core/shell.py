"""ShellSkill — exécute des commandes shell réelles (cmd/bash).

Utilisé par les skills externes qui ont `allowed-tools: Bash(...)` dans leur SKILL.md.
"""

import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from seraphim.skills.base import BaseSkill, SkillResult

logger = logging.getLogger(__name__)

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

    @staticmethod
    def _build_env() -> dict:
        env = os.environ.copy()
        # Ensure npm global bin is in PATH (Windows: not always included by subprocess)
        npm_global = Path.home() / "AppData" / "Roaming" / "npm"
        if npm_global.exists():
            env["PATH"] = str(npm_global) + os.pathsep + env.get("PATH", "")
        return env

    @staticmethod
    def _fix_win_command(cmd: str) -> str:
        """Quote unquoted URLs containing & so PowerShell doesn't split them."""
        return re.sub(
            r'(?<!["\'])https?://\S+',
            lambda m: f'"{m.group(0)}"' if "&" in m.group(0) else m.group(0),
            cmd,
        )

    async def run(self, command: str, timeout: int = 60, **kwargs) -> SkillResult:
        logger.info("shell exec: %s", command)
        try:
            if sys.platform == "win32":
                # cmd.exe ne supporte pas les single quotes — utilise PowerShell
                # Also quote URLs with & params (& = PS call operator when unquoted)
                command = self._fix_win_command(command)
                args = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
                proc = subprocess.run(
                    args,
                    capture_output=True,
                    timeout=timeout,
                    env=self._build_env(),
                )
            else:
                proc = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    timeout=timeout,
                    env=self._build_env(),
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

"""Clipboard skills — read/write Windows clipboard."""

from __future__ import annotations

import subprocess

from seraphim.skills.base import BaseSkill, SkillResult


def _ps(cmd: str, timeout: int = 5) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0, (r.stdout or r.stderr or "").strip()
    except Exception as e:
        return False, str(e)


class ReadClipboardSkill(BaseSkill):
    name = "read_clipboard"
    description = (
        "Lit le contenu du presse-papier Windows. "
        "Utilise quand l'utilisateur dit : 'analyse ce que j'ai copié', "
        "'regarde mon presse-papier', 'lis le clipboard', "
        "'j'ai copié X analyse-le', 'what did I copy', 'check clipboard'."
    )
    parameters = {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs) -> SkillResult:
        ok, text = _ps("Get-Clipboard")
        if not ok:
            return SkillResult(success=False, output="", error=f"Erreur clipboard : {text}")
        if not text:
            return SkillResult(success=True, output="(presse-papier vide)")
        return SkillResult(success=True, output=text)


class WriteClipboardSkill(BaseSkill):
    name = "write_clipboard"
    description = (
        "Copie du texte dans le presse-papier Windows. "
        "Utilise quand l'utilisateur dit : 'copie ça', 'mets dans le clipboard', "
        "'copy this to clipboard'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Texte à copier dans le presse-papier",
            }
        },
        "required": ["text"],
    }

    async def run(self, text: str, **kwargs) -> SkillResult:
        escaped = text.replace("'", "''")
        ok, out = _ps(f"Set-Clipboard -Value '{escaped}'")
        if not ok:
            return SkillResult(success=False, output="", error=f"Erreur clipboard : {out}")
        preview = text[:60] + ("…" if len(text) > 60 else "")
        return SkillResult(success=True, output=f"✓ Copié dans le presse-papier : {preview}")

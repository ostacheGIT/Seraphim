from pathlib import Path
from seraphim.skills.base import BaseSkill, SkillResult


class ListFilesSkill(BaseSkill):
    name = "list_files"
    description = "Liste les fichiers et dossiers dans un répertoire"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Chemin du répertoire (ex: src/seraphim, ~)"}
        },
        "required": ["path"]
    }

    async def run(self, path: str, **kwargs) -> SkillResult:
        try:
            p = Path(path).expanduser()
            if not p.is_absolute():
                p = Path.cwd() / p
            p = p.resolve()
            if not p.exists():
                return SkillResult(success=False, output="", error=f"Répertoire '{p}' introuvable.")
            entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
            lines = []
            for entry in entries:
                prefix = "📄" if entry.is_file() else "📁"
                lines.append(f"{prefix} {entry.name}")
            output = "\n".join(lines) if lines else "(répertoire vide)"
            return SkillResult(success=True, output=output)
        except PermissionError as e:
            return SkillResult(success=False, output="", error=f"Accès refusé : {p}")
        except Exception as e:
            return SkillResult(success=False, output="", error=str(e))

class ReadFileSkill(BaseSkill):
    name = "read_file"
    description = "Lit le contenu d'un fichier"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Chemin du fichier"}
        },
        "required": ["path"]
    }

    async def run(self, path: str, **kwargs) -> SkillResult:
        try:
            content = Path(path).read_text(encoding="utf-8")
            return SkillResult(success=True, output=content)
        except Exception as e:
            return SkillResult(success=False, output="", error=str(e))

class WriteFileSkill(BaseSkill):
    name = "write_file"
    description = "Écrit du contenu dans un fichier"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}
        },
        "required": ["path", "content"]
    }

    async def run(self, path: str = "", content: str = "", **kwargs) -> SkillResult:
        try:
            p = Path(path).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return SkillResult(success=True, output=f"✓ Écrit {len(content)} caractères dans {path}")
        except Exception as e:
            return SkillResult(success=False, output="", error=str(e))
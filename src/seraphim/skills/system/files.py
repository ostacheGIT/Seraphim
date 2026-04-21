from pathlib import Path
from seraphim.skills.base import BaseSkill, SkillResult

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

    async def run(self, path: str = "", f: str = "", **kwargs) -> SkillResult:
        path = path or f  # accepte les deux noms
        try:
            content = Path(path).read_text(encoding="utf-8")
            return SkillResult(success=True, output=content)
        except Exception as e:
            return SkillResult(success=False, output="", error=str(e))
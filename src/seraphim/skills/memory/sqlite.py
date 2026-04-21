import aiosqlite
from pathlib import Path
from seraphim.skills.base import BaseSkill, SkillResult

DB_PATH = Path.home() / ".seraphim" / "memory.db"

class SaveMemorySkill(BaseSkill):
    name = "save_memory"
    description = "Sauvegarde une information en mémoire"
    parameters = {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "value": {"type": "string"}
        },
        "required": ["key", "value"]
    }

    async def run(self, key: str, value: str, **kwargs) -> SkillResult:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS memory (key TEXT PRIMARY KEY, value TEXT)"
            )
            await db.execute(
                "INSERT OR REPLACE INTO memory VALUES (?, ?)", (key, value)
            )
            await db.commit()
        return SkillResult(success=True, output=f"Mémorisé : {key}")

class RecallMemorySkill(BaseSkill):
    name = "recall_memory"
    description = "Récupère une information mémorisée"
    parameters = {
        "type": "object",
        "properties": {"key": {"type": "string"}},
        "required": ["key"]
    }

    async def run(self, key: str, **kwargs) -> SkillResult:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                    "SELECT value FROM memory WHERE key = ?", (key,)
            ) as cur:
                row = await cur.fetchone()
        if row:
            return SkillResult(success=True, output=row[0])
        return SkillResult(success=False, output="", error=f"Clé '{key}' non trouvée")
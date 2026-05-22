import aiosqlite
from pathlib import Path
from seraphim.skills.base import BaseSkill, SkillResult

DB_PATH = Path.home() / ".seraphim" / "memory.db"


class SaveMemorySkill(BaseSkill):
    name = "save_memory"
    description = "Sauvegarde une information en mémoire (clé/valeur simple)"
    parameters = {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "value": {"type": "string"},
        },
        "required": ["key", "value"],
    }

    async def run(self, key: str, value: str, **kwargs) -> SkillResult:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS memory (key TEXT PRIMARY KEY, value TEXT)"
            )
            await db.execute("INSERT OR REPLACE INTO memory VALUES (?, ?)", (key, value))
            await db.commit()
        return SkillResult(success=True, output=f"Mémorisé : {key}")


class RecallMemorySkill(BaseSkill):
    name = "recall_memory"
    description = "Récupère une information mémorisée par clé"
    parameters = {
        "type": "object",
        "properties": {"key": {"type": "string"}},
        "required": ["key"],
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


class MemoryStoreSkill(BaseSkill):
    """Stores a persistent fact about the user (injected automatically into every conversation)."""

    name = "memory_store"
    description = (
        "Mémorise un fait persistant sur l'utilisateur (nom, préférences, projets…). "
        "Ces faits sont injectés automatiquement dans chaque conversation future."
    )
    parameters = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Nom du fait (ex: 'prenom', 'langue_préférée', 'projet_actuel')",
            },
            "value": {
                "type": "string",
                "description": "Valeur du fait",
            },
        },
        "required": ["key", "value"],
    }

    async def run(self, key: str, value: str, **kwargs) -> SkillResult:
        from seraphim.memory.user_facts import save_fact
        await save_fact(key, value)
        return SkillResult(success=True, output=f"Mémorisé : {key} = {value}")


class MemorySearchSkill(BaseSkill):
    """Searches stored user facts by keyword."""

    name = "memory_search"
    description = "Recherche dans les faits mémorisés sur l'utilisateur par mot-clé"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Mot-clé à rechercher"},
        },
        "required": ["query"],
    }

    async def run(self, query: str, **kwargs) -> SkillResult:
        from seraphim.memory.user_facts import search_facts
        facts = await search_facts(query)
        if not facts:
            return SkillResult(success=True, output="Aucun fait trouvé pour cette recherche.")
        lines = [f"{k}: {v}" for k, v in facts.items()]
        return SkillResult(success=True, output="\n".join(lines))


class MemoryRecallSkill(BaseSkill):
    """Recalls all stored user facts."""

    name = "memory_recall"
    description = "Rappelle tous les faits mémorisés sur l'utilisateur"
    parameters = {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs) -> SkillResult:
        from seraphim.memory.user_facts import get_all_facts
        facts = await get_all_facts()
        if not facts:
            return SkillResult(success=True, output="Aucun fait mémorisé pour l'instant.")
        lines = [f"{k}: {v}" for k, v in facts.items()]
        return SkillResult(success=True, output="\n".join(lines))

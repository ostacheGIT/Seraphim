"""Persistent key-value store for user facts, auto-injected into every agent context."""
from __future__ import annotations

import aiosqlite
from pathlib import Path

DB_PATH = Path.home() / ".seraphim" / "user_facts.db"


async def _ensure_table(db: aiosqlite.Connection) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS facts (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    await db.commit()


async def save_fact(key: str, value: str) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await _ensure_table(db)
        await db.execute(
            "INSERT OR REPLACE INTO facts (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            (key.strip(), value.strip()),
        )
        await db.commit()


async def delete_fact(key: str) -> bool:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await _ensure_table(db)
            cur = await db.execute("DELETE FROM facts WHERE key = ?", (key,))
            await db.commit()
            return cur.rowcount > 0
    except Exception:
        return False


async def get_all_facts() -> dict[str, str]:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await _ensure_table(db)
            async with db.execute("SELECT key, value FROM facts ORDER BY key") as cur:
                rows = await cur.fetchall()
        return {row[0]: row[1] for row in rows}
    except Exception:
        return {}


async def search_facts(query: str) -> dict[str, str]:
    try:
        q = f"%{query.lower()}%"
        async with aiosqlite.connect(DB_PATH) as db:
            await _ensure_table(db)
            async with db.execute(
                "SELECT key, value FROM facts WHERE lower(key) LIKE ? OR lower(value) LIKE ? ORDER BY key",
                (q, q),
            ) as cur:
                rows = await cur.fetchall()
        return {row[0]: row[1] for row in rows}
    except Exception:
        return {}


def format_facts_for_prompt(facts: dict[str, str]) -> str:
    if not facts:
        return ""
    lines = [f"- {k}: {v}" for k, v in facts.items()]
    return "Informations connues sur l'utilisateur:\n" + "\n".join(lines)

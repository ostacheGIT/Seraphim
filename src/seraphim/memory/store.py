import aiosqlite
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".seraphim" / "memory.db"


async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
                         CREATE TABLE IF NOT EXISTS conversations (
                                                                      id        INTEGER PRIMARY KEY AUTOINCREMENT,
                                                                      session   TEXT    NOT NULL,
                                                                      agent     TEXT    NOT NULL DEFAULT 'chat',
                                                                      role      TEXT    NOT NULL,
                                                                      content   TEXT    NOT NULL,
                                                                      timestamp TEXT    NOT NULL
                         )
                         """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_session ON conversations(session)"
        )
        # Colonne title optionnelle pour nommer les conversations
        try:
            await db.execute("ALTER TABLE conversations ADD COLUMN title TEXT")
        except Exception:
            pass
        await db.commit()


async def save_message(
        session: str, role: str, content: str, agent: str = "chat"
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO conversations (session, agent, role, content, timestamp) VALUES (?,?,?,?,?)",
            (session, agent, role, content, datetime.now().isoformat()),
        )
        await db.commit()


async def load_history(session: str, limit: int = 50) -> list[dict[str, str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
                "SELECT role, content FROM conversations WHERE session = ? ORDER BY id DESC LIMIT ?",
                (session, limit),
        ) as cursor:
            rows = await cursor.fetchall()
    return [{"role": r, "content": c} for r, c in reversed(rows)]


async def list_sessions() -> list[dict]:
    """Retourne la liste des sessions avec le premier message user comme titre."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
                              SELECT
                                  c.session,
                                  c.agent,
                                  first_msg.content  AS title,
                                  c.timestamp        AS updated_at
                              FROM conversations c
                                       JOIN (
                                  SELECT session, MIN(id) AS min_id
                                  FROM conversations
                                  WHERE role = 'user'
                                  GROUP BY session
                              ) AS fm ON fm.session = c.session
                                       JOIN conversations first_msg ON first_msg.id = fm.min_id
                              WHERE c.id IN (
                                  SELECT MAX(id) FROM conversations GROUP BY session
                                  )
                              ORDER BY c.timestamp DESC
                              """) as cursor:
            rows = await cursor.fetchall()
    return [
        {
            "session":   r[0],
            "agent":     r[1],
            "preview":   r[2][:80] if r[2] else r[0],
            "timestamp": r[3],
        }
        for r in rows
    ]


async def delete_session(session: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM conversations WHERE session = ?", (session,))
        await db.commit()
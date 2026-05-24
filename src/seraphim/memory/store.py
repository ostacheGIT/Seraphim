import asyncio
import logging

import aiosqlite
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

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
            logger.debug("Column 'title' already exists, skipping ALTER TABLE")
        # Sliding summary buffer table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS session_summaries (
                session    TEXT PRIMARY KEY,
                summary    TEXT NOT NULL,
                msg_count  INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT    NOT NULL
            )
        """)
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
    """Retourne la liste des sessions avec le titre LLM ou le premier message user."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
                              SELECT
                                  c.session,
                                  c.agent,
                                  COALESCE(first_msg.title, first_msg.content) AS title,
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


async def search_sessions(query: str) -> list[dict]:
    """Recherche fulltext dans les titres et le contenu des messages."""
    pattern = f"%{query}%"
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT
                c.session,
                c.agent,
                COALESCE(first_msg.title, first_msg.content) AS title,
                c.timestamp AS updated_at
            FROM conversations c
            JOIN (
                SELECT session, MAX(id) AS max_id FROM conversations GROUP BY session
            ) mx ON mx.max_id = c.id
            JOIN (
                SELECT session, MIN(id) AS min_id
                FROM conversations WHERE role = 'user' GROUP BY session
            ) fm ON fm.session = c.session
            JOIN conversations first_msg ON first_msg.id = fm.min_id
            WHERE c.session IN (
                SELECT DISTINCT session FROM conversations
                WHERE lower(content) LIKE lower(?)
                   OR lower(COALESCE(title, '')) LIKE lower(?)
            )
            ORDER BY c.timestamp DESC
            LIMIT 60
        """, (pattern, pattern)) as cursor:
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


async def save_session_title(session: str, title: str) -> None:
    """Sauvegarde un titre généré par LLM sur la première ligne user de la session."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE conversations SET title = ?
            WHERE id = (
                SELECT MIN(id) FROM conversations
                WHERE session = ? AND role = 'user'
            )
        """, (title, session))
        await db.commit()


async def delete_session(session: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM conversations WHERE session = ?", (session,))
        await db.commit()


async def trim_session_if_needed(session: str, max_messages: int = 200) -> int:
    """Delete oldest rows of a session if it exceeds max_messages. Returns rows deleted."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM conversations WHERE session = ?", (session,)
        ) as cur:
            count = (await cur.fetchone())[0]
        if count <= max_messages:
            return 0
        await db.execute(
            """DELETE FROM conversations
               WHERE session = ? AND id NOT IN (
                   SELECT id FROM conversations WHERE session = ?
                   ORDER BY id DESC LIMIT ?
               )""",
            (session, session, max_messages),
        )
        await db.commit()
        return count - max_messages


async def prune_old_sessions(max_age_days: int = 90, max_total_sessions: int = 500) -> dict:
    """Remove sessions older than max_age_days and keep at most max_total_sessions.

    Returns {"deleted_sessions": N, "deleted_messages": M}.
    """
    from datetime import timezone
    cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
    deleted_sessions = 0
    deleted_messages = 0

    async with aiosqlite.connect(DB_PATH) as db:
        # 1. Delete sessions whose last message is older than cutoff
        async with db.execute(
            """SELECT session FROM (
                   SELECT session, MAX(timestamp) AS last_ts FROM conversations
                   GROUP BY session
               ) WHERE last_ts < ?""",
            (cutoff,),
        ) as cur:
            old = [r[0] for r in await cur.fetchall()]
        for sess in old:
            async with db.execute(
                "SELECT COUNT(*) FROM conversations WHERE session = ?", (sess,)
            ) as cur:
                n = (await cur.fetchone())[0]
            await db.execute("DELETE FROM conversations WHERE session = ?", (sess,))
            await db.execute("DELETE FROM session_summaries WHERE session = ?", (sess,))
            deleted_sessions += 1
            deleted_messages += n

        # 2. If still too many sessions, prune the oldest ones
        async with db.execute(
            """SELECT session FROM (
                   SELECT session, MAX(timestamp) AS last_ts FROM conversations
                   GROUP BY session
                   ORDER BY last_ts DESC
               ) LIMIT -1 OFFSET ?""",
            (max_total_sessions,),
        ) as cur:
            overflow = [r[0] for r in await cur.fetchall()]
        for sess in overflow:
            async with db.execute(
                "SELECT COUNT(*) FROM conversations WHERE session = ?", (sess,)
            ) as cur:
                n = (await cur.fetchone())[0]
            await db.execute("DELETE FROM conversations WHERE session = ?", (sess,))
            await db.execute("DELETE FROM session_summaries WHERE session = ?", (sess,))
            deleted_sessions += 1
            deleted_messages += n

        await db.commit()

    return {"deleted_sessions": deleted_sessions, "deleted_messages": deleted_messages}


async def upsert_messages(session: str, messages: list[dict], agent: str = "chat") -> int:
    """Insert messages that are not already present (by role+content fingerprint).

    Used for UI crash-recovery sync. Returns number of rows inserted.
    """
    inserted = 0
    async with aiosqlite.connect(DB_PATH) as db:
        for msg in messages:
            role = str(msg.get("role", "user"))
            content = str(msg.get("content", ""))
            ts = str(msg.get("timestamp", datetime.now().isoformat()))
            # Check for exact duplicate (same session, role, content)
            async with db.execute(
                "SELECT 1 FROM conversations WHERE session=? AND role=? AND content=? LIMIT 1",
                (session, role, content),
            ) as cur:
                exists = await cur.fetchone()
            if not exists:
                await db.execute(
                    "INSERT INTO conversations (session, agent, role, content, timestamp) VALUES (?,?,?,?,?)",
                    (session, agent, role, content, ts),
                )
                inserted += 1
        if inserted:
            await db.commit()
    return inserted


async def truncate_session(session: str, keep_count: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            DELETE FROM conversations
            WHERE session = ? AND id NOT IN (
                SELECT id FROM conversations
                WHERE session = ?
                ORDER BY id ASC
                LIMIT ?
            )
        """, (session, session, keep_count))
        await db.commit()


# ── Sliding summary buffer ────────────────────────────────────────────────────

async def get_session_message_count(session: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM conversations WHERE session = ?", (session,)
        ) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else 0


async def get_session_summary(session: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT summary FROM session_summaries WHERE session = ?", (session,)
        ) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else None


async def save_session_summary(session: str, summary: str, msg_count: int = 0) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO session_summaries (session, summary, msg_count, updated_at)
               VALUES (?, ?, ?, ?)""",
            (session, summary, msg_count, datetime.now().isoformat()),
        )
        await db.commit()


async def load_older_messages_for_summary(session: str, keep_recent: int = 20) -> list[dict]:
    """Return messages that fall outside the recent keep_recent window (oldest first)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT role, content FROM conversations
               WHERE session = ?
               ORDER BY id ASC
               LIMIT (SELECT MAX(0, COUNT(*) - ?) FROM conversations WHERE session = ?)""",
            (session, keep_recent, session),
        ) as cursor:
            rows = await cursor.fetchall()
    return [{"role": r, "content": c} for r, c in rows]


async def load_history_with_summary(
    session: str, keep_recent: int = 20
) -> tuple[list[dict], str | None]:
    """
    Load recent messages + any stored summary of older messages.
    Returns (recent_messages, summary_text_or_None).
    """
    recent, total, summary = await asyncio.gather(
        load_history(session, limit=keep_recent),
        get_session_message_count(session),
        get_session_summary(session),
    )
    if total <= keep_recent + 5:
        return recent, None  # session is short enough — no summary needed
    return recent, summary
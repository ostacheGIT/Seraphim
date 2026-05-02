"""SQLite persistence for monitor_operative."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

_DB_PATH = Path.home() / ".seraphim" / "monitors.db"


async def init_db() -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS monitors (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT    NOT NULL UNIQUE,
                condition     TEXT    NOT NULL,
                interval_secs INTEGER NOT NULL DEFAULT 300,
                action        TEXT    NOT NULL DEFAULT 'notify',
                last_check    REAL,
                last_result   TEXT,
                triggered_count INTEGER NOT NULL DEFAULT 0,
                enabled       INTEGER NOT NULL DEFAULT 1
            )
        """)
        await db.commit()


async def add_monitor(
    name: str,
    condition: str,
    interval_secs: int = 300,
    action: str = "notify",
) -> int:
    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO monitors (name, condition, interval_secs, action) VALUES (?,?,?,?)",
            (name, condition, interval_secs, action),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]


async def list_monitors(enabled_only: bool = False) -> list[dict[str, Any]]:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if enabled_only:
            cur = await db.execute("SELECT * FROM monitors WHERE enabled=1")
        else:
            cur = await db.execute("SELECT * FROM monitors")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_monitor(name: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM monitors WHERE name=?", (name,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def update_check(name: str, result: str, triggered: bool) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        if triggered:
            await db.execute(
                """UPDATE monitors
                   SET last_check=?, last_result=?, triggered_count=triggered_count+1
                   WHERE name=?""",
                (time.time(), result[:2000], name),
            )
        else:
            await db.execute(
                "UPDATE monitors SET last_check=?, last_result=? WHERE name=?",
                (time.time(), result[:2000], name),
            )
        await db.commit()


async def set_enabled(name: str, enabled: bool) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "UPDATE monitors SET enabled=? WHERE name=?",
            (1 if enabled else 0, name),
        )
        await db.commit()


async def delete_monitor(name: str) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("DELETE FROM monitors WHERE name=?", (name,))
        await db.commit()

"""SQLite-backed trace storage for the learning loop."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

_DB_PATH = Path.home() / ".seraphim" / "learning.db"


@dataclass
class TraceStep:
    step: int
    tool: str
    args: dict[str, Any]
    output: str
    latency_ms: float = 0.0


@dataclass
class Trace:
    agent: str
    query: str
    final_response: str
    steps: list[TraceStep] = field(default_factory=list)
    session_id: str = ""
    success: bool = True
    feedback: float = -1.0      # -1 = unknown, 0.0–1.0 explicit
    latency_ms: float = 0.0
    tokens_in: int = 0           # estimated prompt tokens
    tokens_out: int = 0          # estimated completion tokens
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


async def init_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS traces (
                id            TEXT PRIMARY KEY,
                session_id    TEXT NOT NULL DEFAULT '',
                agent         TEXT NOT NULL,
                query         TEXT NOT NULL,
                steps_json    TEXT NOT NULL DEFAULT '[]',
                final_response TEXT NOT NULL DEFAULT '',
                success       INTEGER NOT NULL DEFAULT 1,
                feedback      REAL    NOT NULL DEFAULT -1,
                latency_ms    REAL    NOT NULL DEFAULT 0,
                metadata_json TEXT    NOT NULL DEFAULT '{}',
                timestamp     TEXT    NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sft_pairs (
                id            TEXT PRIMARY KEY,
                trace_id      TEXT NOT NULL UNIQUE,
                agent         TEXT NOT NULL,
                instruction   TEXT NOT NULL,
                response      TEXT NOT NULL,
                quality_score REAL NOT NULL DEFAULT 0,
                exported      INTEGER NOT NULL DEFAULT 0,
                timestamp     TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS prompt_overlays (
                target        TEXT PRIMARY KEY,
                overlay_json  TEXT NOT NULL,
                score_before  REAL NOT NULL DEFAULT 0,
                score_after   REAL NOT NULL DEFAULT 0,
                accepted      INTEGER NOT NULL DEFAULT 0,
                timestamp     TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS prompt_overlays_history (
                id            TEXT PRIMARY KEY,
                target        TEXT NOT NULL,
                overlay_json  TEXT NOT NULL,
                score_before  REAL NOT NULL DEFAULT 0,
                score_after   REAL NOT NULL DEFAULT 0,
                improvement   REAL NOT NULL DEFAULT 0,
                latency_before_ms REAL NOT NULL DEFAULT 0,
                latency_after_ms  REAL NOT NULL DEFAULT 0,
                accepted      INTEGER NOT NULL DEFAULT 0,
                timestamp     TEXT NOT NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_traces_agent ON traces(agent)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_traces_feedback ON traces(feedback)")
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_sft_trace_id ON sft_pairs(trace_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_overlay_hist_target ON prompt_overlays_history(target)"
        )
        # Migrate existing traces table to add token columns
        for col in ("tokens_in INTEGER NOT NULL DEFAULT 0", "tokens_out INTEGER NOT NULL DEFAULT 0"):
            try:
                await db.execute(f"ALTER TABLE traces ADD COLUMN {col}")
            except Exception:
                pass  # column already exists
        await db.commit()


async def save_trace(trace: Trace) -> None:
    await init_db()
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO traces
               (id, session_id, agent, query, steps_json, final_response,
                success, feedback, latency_ms, tokens_in, tokens_out,
                metadata_json, timestamp)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                trace.id, trace.session_id, trace.agent, trace.query,
                json.dumps([vars(s) for s in trace.steps]),
                trace.final_response,
                int(trace.success), trace.feedback, trace.latency_ms,
                trace.tokens_in, trace.tokens_out,
                json.dumps(trace.metadata), trace.timestamp,
            ),
        )
        await db.commit()


async def load_traces(
    agent: str | None = None,
    min_feedback: float = -1.0,
    limit: int = 200,
) -> list[Trace]:
    await init_db()
    conditions = []
    params: list[Any] = []
    if agent:
        conditions.append("agent = ?")
        params.append(agent)
    if min_feedback >= 0:
        conditions.append("(feedback >= ? OR feedback = -1)")
        params.append(min_feedback)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT * FROM traces {where} ORDER BY timestamp DESC LIMIT ?", params
        )
        rows = await cur.fetchall()
    return [_row_to_trace(dict(r)) for r in rows]


async def set_feedback(trace_id: str, feedback: float) -> None:
    await init_db()
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "UPDATE traces SET feedback=? WHERE id=?", (feedback, trace_id)
        )
        await db.commit()


async def save_sft_pair(
    trace_id: str, agent: str, instruction: str, response: str, quality: float
) -> bool:
    """Insert SFT pair. Returns True if newly inserted, False if trace_id already exists."""
    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute(
            """INSERT OR IGNORE INTO sft_pairs
               (id, trace_id, agent, instruction, response, quality_score, timestamp)
               VALUES (?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), trace_id, agent, instruction, response,
             quality, datetime.now().isoformat()),
        )
        await db.commit()
        return cur.rowcount > 0


async def load_sft_pairs(
    agent: str | None = None,
    min_quality: float = 0.5,
    limit: int = 500,
) -> list[dict[str, Any]]:
    conditions = ["quality_score >= ?"]
    params: list[Any] = [min_quality]
    if agent:
        conditions.append("agent = ?")
        params.append(agent)
    params.append(limit)
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT * FROM sft_pairs WHERE {' AND '.join(conditions)} ORDER BY quality_score DESC LIMIT ?",
            params,
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def save_overlay(
    target: str,
    overlay: dict,
    score_before: float,
    score_after: float,
    accepted: bool,
    latency_before_ms: float = 0.0,
    latency_after_ms: float = 0.0,
) -> None:
    ts = datetime.now().isoformat()
    overlay_json = json.dumps(overlay)
    improvement = score_after - score_before
    async with aiosqlite.connect(_DB_PATH) as db:
        # Update active overlay (one row per target)
        await db.execute(
            """INSERT OR REPLACE INTO prompt_overlays
               (target, overlay_json, score_before, score_after, accepted, timestamp)
               VALUES (?,?,?,?,?,?)""",
            (target, overlay_json, score_before, score_after, int(accepted), ts),
        )
        # Append to history (never overwritten)
        await db.execute(
            """INSERT INTO prompt_overlays_history
               (id, target, overlay_json, score_before, score_after, improvement,
                latency_before_ms, latency_after_ms, accepted, timestamp)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), target, overlay_json, score_before, score_after,
             improvement, latency_before_ms, latency_after_ms, int(accepted), ts),
        )
        await db.commit()


async def load_overlay(target: str) -> dict | None:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM prompt_overlays WHERE target=? AND accepted=1", (target,)
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return json.loads(dict(row)["overlay_json"])


async def load_overlay_history(target: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return all historical overlay runs for a target, newest first."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT id, target, score_before, score_after, improvement,
                      latency_before_ms, latency_after_ms, accepted, timestamp
               FROM prompt_overlays_history
               WHERE target=?
               ORDER BY timestamp DESC LIMIT ?""",
            (target, limit),
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def trace_stats() -> dict[str, Any]:
    await init_db()
    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM traces")
        total = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM traces WHERE feedback >= 0.7")
        good = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM sft_pairs")
        pairs = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM prompt_overlays WHERE accepted=1")
        overlays = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COALESCE(SUM(tokens_out), 0) FROM traces")
        total_tokens_out = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM prompt_overlays_history")
        overlay_runs = (await cur.fetchone())[0]
    return {
        "total_traces": total,
        "good_traces": good,
        "sft_pairs": pairs,
        "accepted_overlays": overlays,
        "total_tokens_out": int(total_tokens_out),
        "overlay_runs": overlay_runs,
    }


def _row_to_trace(r: dict) -> Trace:
    steps_raw = json.loads(r["steps_json"])
    steps = [TraceStep(**s) for s in steps_raw]
    return Trace(
        id=r["id"],
        session_id=r["session_id"],
        agent=r["agent"],
        query=r["query"],
        steps=steps,
        final_response=r["final_response"],
        success=bool(r["success"]),
        feedback=r["feedback"],
        latency_ms=r["latency_ms"],
        tokens_in=r.get("tokens_in", 0) or 0,
        tokens_out=r.get("tokens_out", 0) or 0,
        metadata=json.loads(r["metadata_json"]),
        timestamp=r["timestamp"],
    )

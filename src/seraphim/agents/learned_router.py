"""Learned router — overrides static regex routing using per-class trace statistics.

For each (query_class, agent) pair we maintain running stats (sample_count,
total_score, total_latency_ms). At routing time, if the empirically best agent
has enough samples and outperforms the static choice by min_advantage, we
override the decision.

Score signal (saved on each trace):
  explicit feedback (0-1) > success heuristic (0.7 success / 0.3 failure)
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from seraphim.agents.router import RoutingDecision

# Import patterns directly from router — single source of truth, no drift
from seraphim.agents.router import (
    _SYSTEM_RE,
    _CODE_RE,
    _CODEACT_RE,
    _FILE_RE,
    _MATH_RE,
    _MATH_FNS,
    _HTTP_RE,
    _WEB_RE,
    _MEMORY_RE,
    _THINK_RE,
)

_DB_PATH = Path.home() / ".seraphim" / "learning.db"
_table_ready = False

# ── Query classes ─────────────────────────────────────────────────────────────

QUERY_CLASSES = [
    "system", "code_exec", "code_gen", "file",
    "math", "http", "web", "memory", "think", "chat",
]


def classify_query(query: str) -> str:
    """Map query to one of QUERY_CLASSES."""
    q = query.strip()
    if _SYSTEM_RE.search(q):
        return "system"
    if _CODEACT_RE.search(q):
        return "code_exec"
    if _CODE_RE.search(q):
        if re.search(r"(?:exécute|run|execute|lance)\s+(?:ce\s+)?(?:code|script)", q, re.I):
            return "code_exec"
        return "code_gen"
    if _FILE_RE.search(q):
        return "file"
    m = _MATH_RE.match(q)
    if m:
        words = re.findall(r"[a-zA-Z]{3,}", m.group("expr").strip())
        if all(w.lower() in _MATH_FNS for w in words):
            return "math"
    if _HTTP_RE.search(q):
        return "http"
    if _WEB_RE.search(q):
        return "web"
    if _MEMORY_RE.search(q):
        return "memory"
    if _THINK_RE.search(q) or len(q.split()) > 30:
        return "think"
    return "chat"


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _ensure_table_in_conn(db: aiosqlite.Connection) -> None:
    global _table_ready
    if _table_ready:
        return
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS routing_stats (
            query_class      TEXT NOT NULL,
            agent            TEXT NOT NULL,
            sample_count     INTEGER NOT NULL DEFAULT 0,
            total_score      REAL    NOT NULL DEFAULT 0.0,
            total_latency_ms REAL    NOT NULL DEFAULT 0.0,
            last_updated     TEXT    NOT NULL,
            PRIMARY KEY (query_class, agent)
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_routing_class ON routing_stats(query_class)"
    )
    await db.commit()
    _table_ready = True


async def _ensure_table() -> None:
    if _table_ready:
        return
    async with aiosqlite.connect(_DB_PATH) as db:
        await _ensure_table_in_conn(db)


async def update_routing_stats(
    agent: str,
    query_class: str,
    score: float,
    latency_ms: float = 0.0,
) -> None:
    """Upsert running stats for (query_class, agent). Called after each trace."""
    now = datetime.now().isoformat()
    async with aiosqlite.connect(_DB_PATH) as db:
        await _ensure_table_in_conn(db)
        await db.execute(
            """
            INSERT INTO routing_stats (query_class, agent, sample_count, total_score, total_latency_ms, last_updated)
            VALUES (?, ?, 1, ?, ?, ?)
            ON CONFLICT(query_class, agent) DO UPDATE SET
                sample_count     = sample_count + 1,
                total_score      = total_score + excluded.total_score,
                total_latency_ms = total_latency_ms + excluded.total_latency_ms,
                last_updated     = excluded.last_updated
            """,
            (query_class, agent, score, latency_ms, now),
        )
        await db.commit()


async def get_routing_stats() -> list[dict]:
    """Return all routing stats as list of dicts (sorted by query_class, mean_score desc)."""
    async with aiosqlite.connect(_DB_PATH) as db:
        await _ensure_table_in_conn(db)
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT
                query_class,
                agent,
                sample_count,
                ROUND(total_score / sample_count, 3)      AS mean_score,
                ROUND(total_latency_ms / sample_count, 0) AS mean_latency_ms,
                last_updated
            FROM routing_stats
            ORDER BY query_class, mean_score DESC
        """) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ── Learned routing decision ──────────────────────────────────────────────────

async def learned_route(
    query: str,
    static_agent: str,
    min_samples: int = 5,
    min_advantage: float = 0.05,
) -> "RoutingDecision | None":
    """
    Return an override RoutingDecision if learned stats show a better agent.
    Returns None if not enough data or static agent is already best.

    min_samples: minimum traces per (class, agent) before trusting stats
    min_advantage: how much better the learned agent must be (score delta)
    """
    from seraphim.agents.router import RoutingDecision

    query_class = classify_query(query)

    # System/math/http commands use direct-pattern bypass — learned routing would
    # route away from react and break the bypass entirely.
    if query_class in ("system", "math", "http"):
        return None

    async with aiosqlite.connect(_DB_PATH) as db:
        await _ensure_table_in_conn(db)
        db.row_factory = aiosqlite.Row
        # Get all agents for this class with enough samples
        async with db.execute(
            """
            SELECT agent,
                   total_score / sample_count AS mean_score,
                   total_latency_ms / sample_count AS mean_latency_ms,
                   sample_count
            FROM routing_stats
            WHERE query_class = ? AND sample_count >= ?
            ORDER BY mean_score DESC
            """,
            (query_class, min_samples),
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        return None

    best = rows[0]
    best_agent = best["agent"]
    best_score = best["mean_score"]

    # Get static agent's score if we have it
    static_score = next(
        (r["mean_score"] for r in rows if r["agent"] == static_agent),
        None,
    )

    # If static agent is already best (or tied within threshold), don't override
    if best_agent == static_agent:
        return None
    if static_score is not None and (best_score - static_score) < min_advantage:
        return None

    advantage = best_score - (static_score or 0.0)
    return RoutingDecision(
        agent=best_agent,
        skill=best_agent.split(":")[1] if best_agent.startswith("skill:") else None,
        reason=(
            f"learned({query_class}): {best_agent} "
            f"score={best_score:.3f} vs {static_agent} "
            f"score={f'{static_score:.3f}' if static_score is not None else '?'} "
            f"Δ={advantage:+.3f} n={best['sample_count']}"
        ),
    )


__all__ = [
    "QUERY_CLASSES",
    "classify_query",
    "update_routing_stats",
    "get_routing_stats",
    "learned_route",
]

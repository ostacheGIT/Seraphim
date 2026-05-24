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
            query_class       TEXT NOT NULL,
            agent             TEXT NOT NULL,
            sample_count      INTEGER NOT NULL DEFAULT 0,
            total_score       REAL    NOT NULL DEFAULT 0.0,
            total_latency_ms  REAL    NOT NULL DEFAULT 0.0,
            total_tokens_out  REAL    NOT NULL DEFAULT 0.0,
            last_updated      TEXT    NOT NULL,
            PRIMARY KEY (query_class, agent)
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_routing_class ON routing_stats(query_class)"
    )
    # Migration: add total_tokens_out if the table predates this column
    try:
        await db.execute(
            "ALTER TABLE routing_stats ADD COLUMN total_tokens_out REAL NOT NULL DEFAULT 0.0"
        )
    except Exception:
        pass  # column already exists
    await db.commit()
    _table_ready = True


async def _ensure_table() -> None:
    if _table_ready:
        return
    async with aiosqlite.connect(_DB_PATH) as db:
        await _ensure_table_in_conn(db)


# ── Multi-constraint composite score ─────────────────────────────────────────

_QUALITY_W = 0.60   # accuracy is the primary objective
_SPEED_W   = 0.30   # latency matters for UX
_COST_W    = 0.10   # token efficiency as tie-breaker
_REF_LATENCY_MS = 10_000.0   # 10 s → speed score of 0
_REF_TOKENS     = 2_000.0    # 2 000 tokens → efficiency score of 0


def _composite_score(
    mean_score: float,
    mean_latency_ms: float,
    mean_tokens: float,
) -> float:
    """Weighted multi-constraint score: quality (60%) + speed (30%) + efficiency (10%)."""
    speed      = 1.0 - min(1.0, mean_latency_ms / _REF_LATENCY_MS)
    efficiency = 1.0 - min(1.0, mean_tokens / _REF_TOKENS)
    return _QUALITY_W * mean_score + _SPEED_W * speed + _COST_W * efficiency


async def update_routing_stats(
    agent: str,
    query_class: str,
    score: float,
    latency_ms: float = 0.0,
    tokens_out: float = 0.0,
) -> None:
    """Upsert running stats for (query_class, agent). Called after each trace."""
    now = datetime.now().isoformat()
    async with aiosqlite.connect(_DB_PATH) as db:
        await _ensure_table_in_conn(db)
        await db.execute(
            """
            INSERT INTO routing_stats
                (query_class, agent, sample_count, total_score, total_latency_ms, total_tokens_out, last_updated)
            VALUES (?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(query_class, agent) DO UPDATE SET
                sample_count     = sample_count + 1,
                total_score      = total_score      + excluded.total_score,
                total_latency_ms = total_latency_ms + excluded.total_latency_ms,
                total_tokens_out = total_tokens_out + excluded.total_tokens_out,
                last_updated     = excluded.last_updated
            """,
            (query_class, agent, score, latency_ms, tokens_out, now),
        )
        await db.commit()


async def get_routing_stats() -> list[dict]:
    """Return all routing stats with composite scores (sorted by query_class, composite desc)."""
    async with aiosqlite.connect(_DB_PATH) as db:
        await _ensure_table_in_conn(db)
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT
                query_class,
                agent,
                sample_count,
                ROUND(total_score       / sample_count, 3) AS mean_score,
                ROUND(total_latency_ms  / sample_count, 0) AS mean_latency_ms,
                ROUND(total_tokens_out  / sample_count, 0) AS mean_tokens_out,
                last_updated
            FROM routing_stats
            ORDER BY query_class, mean_score DESC
        """) as cur:
            rows = await cur.fetchall()

    result = []
    for r in rows:
        d = dict(r)
        d["composite_score"] = round(
            _composite_score(d["mean_score"], d["mean_latency_ms"], d["mean_tokens_out"]), 3
        )
        result.append(d)
    return result


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
        async with db.execute(
            """
            SELECT agent,
                   total_score       / sample_count AS mean_score,
                   total_latency_ms  / sample_count AS mean_latency_ms,
                   total_tokens_out  / sample_count AS mean_tokens_out,
                   sample_count
            FROM routing_stats
            WHERE query_class = ? AND sample_count >= ?
            """,
            (query_class, min_samples),
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        return None

    # Rank by multi-constraint composite score (quality + speed + efficiency)
    ranked = sorted(
        rows,
        key=lambda r: _composite_score(r["mean_score"], r["mean_latency_ms"], r["mean_tokens_out"]),
        reverse=True,
    )

    best = ranked[0]
    best_agent = best["agent"]
    best_composite = _composite_score(best["mean_score"], best["mean_latency_ms"], best["mean_tokens_out"])

    static_row = next((r for r in ranked if r["agent"] == static_agent), None)
    static_composite = (
        _composite_score(static_row["mean_score"], static_row["mean_latency_ms"], static_row["mean_tokens_out"])
        if static_row else None
    )

    if best_agent == static_agent:
        return None
    if static_composite is not None and (best_composite - static_composite) < min_advantage:
        return None

    advantage = best_composite - (static_composite or 0.0)
    return RoutingDecision(
        agent=best_agent,
        skill=best_agent.split(":")[1] if best_agent.startswith("skill:") else None,
        reason=(
            f"learned({query_class}): {best_agent} "
            f"composite={best_composite:.3f} [q={best['mean_score']:.2f} "
            f"lat={best['mean_latency_ms']:.0f}ms tok={best['mean_tokens_out']:.0f}] "
            f"vs {static_agent} composite={f'{static_composite:.3f}' if static_composite else '?'} "
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

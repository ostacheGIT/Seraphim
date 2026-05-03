"""Knowledge distillation — use a frontier/teacher model to generate ideal responses
for queries where the local model underperforms, then store them as high-quality SFT pairs.

Pipeline:
  1. Diagnose  — find weak query classes (low mean_score in routing_stats)
  2. Fetch     — pull real low-score traces for those classes
  3. Generate  — call teacher model to produce ideal responses
  4. Store     — save as SFT pairs (quality=0.9 by default)

Teacher backends: "ollama" (bigger local model) | "anthropic" | "openai"
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import aiosqlite
import httpx

from seraphim.agents.learned_router import classify_query
from seraphim.learning.trace_store import _DB_PATH, save_sft_pair

logger = logging.getLogger(__name__)

# ── Config & Result ───────────────────────────────────────────────────────────

@dataclass
class DistillConfig:
    # Teacher model
    teacher_type: str = "ollama"               # "ollama" | "anthropic" | "openai"
    teacher_model: str = "qwen2.5:14b"         # local big model or API model ID
    teacher_api_key: str = ""                  # falls back to env ANTHROPIC_API_KEY / OPENAI_API_KEY
    teacher_base_url: str = ""                 # custom OpenAI-compatible endpoint

    # What to distill
    min_score_threshold: float = 0.5           # classes below this score are "weak"
    min_samples: int = 3                       # min traces before considering a class weak
    max_real_queries: int = 10                 # real low-score traces per weak class
    synthetic_per_class: int = 0              # synthetic queries to generate per class (0=off)
    target_agents: list[str] = field(default_factory=list)  # [] = all agents

    # Quality
    sft_quality: float = 0.9                  # quality score for distilled SFT pairs
    dry_run: bool = False                      # generate but don't save


@dataclass
class DistillResult:
    success: bool
    weak_classes: list[str]
    queries_processed: int
    pairs_saved: int
    pairs_skipped: int
    message: str = ""
    errors: list[str] = field(default_factory=list)


# ── Teacher call ──────────────────────────────────────────────────────────────

async def _call_teacher(messages: list[dict], config: DistillConfig) -> str:
    """Call teacher model, return response text."""
    t = config.teacher_type

    if t == "ollama":
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "http://localhost:11434/api/chat",
                json={"model": config.teacher_model, "messages": messages, "stream": False},
            )
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "").strip()

    if t == "anthropic":
        api_key = config.teacher_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        # Separate system from user messages
        system = next((m["content"] for m in messages if m["role"] == "system"), None)
        user_msgs = [m for m in messages if m["role"] != "system"]
        payload: dict[str, Any] = {
            "model": config.teacher_model,
            "max_tokens": 1024,
            "messages": user_msgs,
        }
        if system:
            payload["system"] = system
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["content"][0]["text"].strip()

    if t == "openai":
        api_key = config.teacher_api_key or os.environ.get("OPENAI_API_KEY", "")
        base_url = config.teacher_base_url or "https://api.openai.com"
        headers = {"Authorization": f"Bearer {api_key}", "content-type": "application/json"}
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/v1/chat/completions",
                headers=headers,
                json={"model": config.teacher_model, "messages": messages},
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()

    raise ValueError(f"Unknown teacher_type: {t!r}. Use 'ollama', 'anthropic', or 'openai'.")


# ── Diagnosis ─────────────────────────────────────────────────────────────────

async def diagnose_weak_classes(
    min_score: float, min_samples: int
) -> list[dict]:
    """Return query classes where the best agent's mean_score < min_score."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT query_class,
                   MAX(total_score / sample_count) AS best_score,
                   SUM(sample_count)               AS total_samples
            FROM routing_stats
            GROUP BY query_class
            HAVING total_samples >= ? AND best_score < ?
            ORDER BY best_score ASC
            """,
            (min_samples, min_score),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _fetch_weak_traces(query_class: str, limit: int) -> list[dict]:
    """Fetch real traces for a query class sorted by score ascending (worst first)."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT t.id, t.query, t.final_response, t.agent,
                   CASE WHEN t.feedback >= 0 THEN t.feedback
                        WHEN t.success = 0   THEN 0.3
                        ELSE 0.7 END AS score
            FROM traces t
            ORDER BY score ASC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()

    # Filter by query class
    result = []
    for r in rows:
        if classify_query(r["query"]) == query_class:
            result.append(dict(r))
        if len(result) >= limit:
            break
    return result


async def _generate_synthetic_queries(
    query_class: str, n: int, config: DistillConfig
) -> list[str]:
    """Ask teacher to generate n synthetic queries for query_class."""
    prompt = (
        f"Generate exactly {n} diverse, realistic user queries that belong to the "
        f'"{query_class}" category for an AI assistant. '
        "Output ONLY the queries, one per line, no numbering, no explanations."
    )
    try:
        raw = await _call_teacher([{"role": "user", "content": prompt}], config)
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        return lines[:n]
    except Exception as exc:
        logger.warning("Synthetic query generation failed for %s: %s", query_class, exc)
        return []


# ── Core distillation ─────────────────────────────────────────────────────────

_IDEAL_RESPONSE_SYSTEM = (
    "You are an expert AI assistant. Your task is to produce an ideal, accurate, "
    "concise response to a user query. The current AI gave a suboptimal response — "
    "yours should be clearly better: more accurate, more helpful, well-structured."
)


async def _distill_query(
    query: str,
    bad_response: str,
    agent: str,
    config: DistillConfig,
) -> tuple[bool, str]:
    """
    Call teacher with the query + bad response context.
    Returns (saved: bool, ideal_response: str).
    """
    messages = [
        {"role": "system", "content": _IDEAL_RESPONSE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"User query: {query}\n\n"
                f"Current AI response (suboptimal):\n{bad_response[:600]}\n\n"
                "Provide the ideal response:"
            ),
        },
    ]
    ideal = await _call_teacher(messages, config)
    if not ideal or len(ideal) < 10:
        return False, ""

    if config.dry_run:
        return True, ideal

    trace_id = f"distill_{uuid.uuid4().hex[:12]}"
    saved = await save_sft_pair(
        trace_id=trace_id,
        agent=agent,
        instruction=query,
        response=ideal,
        quality=config.sft_quality,
    )
    return saved, ideal


# ── Public entry point ────────────────────────────────────────────────────────

async def run_distillation(config: DistillConfig | None = None) -> DistillResult:
    """Full distillation pipeline: diagnose → fetch → generate → store."""
    if config is None:
        config = DistillConfig()

    logger.info(
        "[Distill] Starting — teacher=%s/%s threshold=%.2f",
        config.teacher_type, config.teacher_model, config.min_score_threshold,
    )

    # Step 1 — Diagnose weak classes
    weak = await diagnose_weak_classes(config.min_score_threshold, config.min_samples)
    if not weak:
        return DistillResult(
            success=True,
            weak_classes=[],
            queries_processed=0,
            pairs_saved=0,
            pairs_skipped=0,
            message="No weak query classes found — model performing well everywhere.",
        )

    weak_class_names = [w["query_class"] for w in weak]
    logger.info("[Distill] Weak classes: %s", weak_class_names)

    queries_processed = 0
    pairs_saved = 0
    pairs_skipped = 0
    errors: list[str] = []

    for item in weak:
        qclass = item["query_class"]
        logger.info(
            "[Distill] Processing class '%s' (best_score=%.3f samples=%d)",
            qclass, item["best_score"], item["total_samples"],
        )

        # Step 2 — Fetch real weak traces
        traces = await _fetch_weak_traces(qclass, config.max_real_queries)

        # Step 3 — Optional synthetic queries
        synthetic_queries: list[str] = []
        if config.synthetic_per_class > 0:
            synthetic_queries = await _generate_synthetic_queries(
                qclass, config.synthetic_per_class, config
            )
            logger.info("[Distill]   Generated %d synthetic queries", len(synthetic_queries))

        # Step 4 — Distill real traces
        for t in traces:
            try:
                saved, _ = await _distill_query(
                    t["query"], t["final_response"],
                    t["agent"] or "chat", config,
                )
                queries_processed += 1
                if saved:
                    pairs_saved += 1
                    logger.debug("[Distill]   ✓ saved pair for: '%s…'", t["query"][:50])
                else:
                    pairs_skipped += 1
            except Exception as exc:
                msg = f"{qclass}/{t['query'][:40]}: {exc}"
                logger.warning("[Distill]   ✗ %s", msg)
                errors.append(msg)

        # Step 4b — Distill synthetic queries (use empty string as "bad response")
        for sq in synthetic_queries:
            try:
                messages = [
                    {"role": "system", "content": _IDEAL_RESPONSE_SYSTEM},
                    {"role": "user", "content": sq},
                ]
                ideal = await _call_teacher(messages, config)
                if ideal and len(ideal) >= 10 and not config.dry_run:
                    trace_id = f"distill_syn_{uuid.uuid4().hex[:12]}"
                    saved = await save_sft_pair(
                        trace_id=trace_id,
                        agent="chat",
                        instruction=sq,
                        response=ideal,
                        quality=config.sft_quality,
                    )
                    queries_processed += 1
                    if saved:
                        pairs_saved += 1
            except Exception as exc:
                errors.append(f"synthetic/{qclass}: {exc}")

    logger.info(
        "[Distill] Done — processed=%d saved=%d skipped=%d errors=%d",
        queries_processed, pairs_saved, pairs_skipped, len(errors),
    )

    return DistillResult(
        success=True,
        weak_classes=weak_class_names,
        queries_processed=queries_processed,
        pairs_saved=pairs_saved,
        pairs_skipped=pairs_skipped,
        errors=errors,
        message=(
            f"Distilled {pairs_saved} pairs from {len(weak_class_names)} weak classes "
            f"({queries_processed} queries processed)"
        ),
    )


__all__ = ["DistillConfig", "DistillResult", "run_distillation", "diagnose_weak_classes"]

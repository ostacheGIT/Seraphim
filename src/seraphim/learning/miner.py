"""Mine SFT training pairs from accumulated traces."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from seraphim.learning.trace_store import (
    Trace,
    load_traces,
    save_sft_pair,
    load_sft_pairs,
)

logger = logging.getLogger(__name__)

_BAD_PATTERNS = [
    "unable to complete",
    "i was unable",
    "erreur :",
    "error:",
    "skill error",
    "backend error",
]

_MIN_RESPONSE_LEN = 40


def _quality_score(trace: Trace) -> float:
    """Heuristic quality score 0.0–1.0 for a trace."""
    if not trace.success:
        return 0.0

    # Explicit feedback always wins over heuristics
    if trace.feedback >= 0:
        return trace.feedback

    resp = trace.final_response.lower()
    if len(trace.final_response) < _MIN_RESPONSE_LEN:
        return 0.0
    for pat in _BAD_PATTERNS:
        if pat in resp:
            return 0.0

    score = 0.6

    # Bonus: used tools successfully
    if trace.steps:
        score += 0.1

    # Bonus: substantive response
    if len(trace.final_response) > 200:
        score += 0.1

    # Bonus: no error steps
    if not any("error" in s.output.lower() for s in trace.steps):
        score += 0.1

    # Penalty: fallback message
    if "i was unable" in resp or "unable to complete" in resp:
        score -= 0.4

    return max(0.0, min(1.0, score))


async def mine(
    agent: str | None = None,
    min_quality: float = 0.6,
    limit: int = 200,
    system_prompt: str = "",
) -> int:
    """Extract SFT pairs from traces. Returns count of new pairs saved."""
    traces = await load_traces(agent=agent, limit=limit)
    saved = 0
    skipped = 0
    for trace in traces:
        score = _quality_score(trace)
        if score < min_quality:
            continue
        instruction = _build_instruction(trace, system_prompt)
        inserted = await save_sft_pair(
            trace_id=trace.id,
            agent=trace.agent,
            instruction=instruction,
            response=trace.final_response,
            quality=score,
        )
        if inserted:
            saved += 1
        else:
            skipped += 1
    logger.info(
        "Mined %d new SFT pairs from %d traces (min_quality=%.2f, skipped_dupes=%d)",
        saved, len(traces), min_quality, skipped,
    )
    return saved


def _build_instruction(trace: Trace, system_prompt: str = "") -> str:
    parts = []
    if system_prompt:
        parts.append(f"[SYSTEM] {system_prompt}")
    parts.append(f"[USER] {trace.query}")
    if trace.steps:
        for s in trace.steps:
            parts.append(f"[TOOL:{s.tool}] {json.dumps(s.args)}")
            parts.append(f"[RESULT] {s.output[:500]}")
    return "\n".join(parts)


async def export_jsonl(path: str, agent: str | None = None, min_quality: float = 0.6) -> int:
    """Export SFT pairs as JSONL for external fine-tuning."""
    pairs = await load_sft_pairs(agent=agent, min_quality=min_quality)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps({
                "instruction": p["instruction"],
                "response": p["response"],
                "quality": p["quality_score"],
            }, ensure_ascii=False) + "\n")
    logger.info("Exported %d SFT pairs to %s", len(pairs), path)
    return len(pairs)

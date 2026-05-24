"""LLM-as-judge evaluator — scores agent responses before/after optimization."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

_JUDGE_PROMPT = """\
You are an impartial evaluator. Score the following AI response on a scale of 1-5.

Criteria:
- 5: Excellent — accurate, complete, concise, directly addresses the question
- 4: Good — mostly correct, minor gaps
- 3: Adequate — partially answers, some errors or verbosity
- 2: Poor — mostly wrong or unhelpful
- 1: Useless — irrelevant, empty, or error message

Question: {query}
Response: {response}

Reply with ONLY a single integer (1-5). Nothing else."""

_BATCH_JUDGE_SYS = (
    "You are a strict impartial evaluator. "
    "Reply with ONLY a JSON array of integers, one score (1-5) per item. Nothing else."
)

_DEFAULT_TEST_QUERIES = [
    "What is 2 + 2?",
    "List 3 benefits of exercise.",
    "What is the capital of France?",
    "Explain what RAM is in one sentence.",
    "How do I reverse a list in Python?",
]


async def score_response(query: str, response: str, engine=None) -> float:
    """Score a single (query, response) pair using LLM-as-judge. Returns 0.0–1.0."""
    if engine is None:
        from seraphim.engine import get_engine
        engine = get_engine()

    prompt = _JUDGE_PROMPT.format(query=query, response=response[:800])
    try:
        result = await engine.chat([
            {"role": "system", "content": "You are a strict impartial evaluator. Reply with only a number."},
            {"role": "user", "content": prompt},
        ])
        msgs = result.get("messages", []) if isinstance(result, dict) else getattr(result, "messages", [])
        raw = msgs[-1].get("content", "3").strip() if msgs else "3"
        score = int(raw[0]) if raw and raw[0].isdigit() else 3
        return max(1, min(5, score)) / 5.0
    except Exception as e:
        logger.warning("Judge scoring failed: %s", e)
        return 0.5


async def batch_score_responses(
    queries: list[str],
    responses: list[str],
    engine=None,
) -> list[float]:
    """Score multiple (query, response) pairs in a single LLM call. Returns 0.0–1.0 per pair."""
    if engine is None:
        from seraphim.engine import get_engine
        engine = get_engine()

    if not queries:
        return []

    items = "\n\n".join(
        f"[{i}] Q: {q}\nA: {r[:400]}"
        for i, (q, r) in enumerate(zip(queries, responses), 1)
    )
    prompt = (
        f"Score each response 1-5. Respond with ONLY a JSON array of {len(queries)} integers.\n\n"
        + items
    )
    try:
        result = await engine.chat([
            {"role": "system", "content": _BATCH_JUDGE_SYS},
            {"role": "user", "content": prompt},
        ])
        msgs = result.get("messages", []) if isinstance(result, dict) else getattr(result, "messages", [])
        raw = msgs[-1].get("content", "").strip() if msgs else ""
        m = re.search(r"\[[\d,\s]+\]", raw)
        if m:
            scores = json.loads(m.group())
            if len(scores) == len(queries):
                return [max(1, min(5, int(s))) / 5.0 for s in scores]
    except Exception as e:
        logger.warning("Batch scoring failed (%s), falling back to individual scoring", e)

    return [await score_response(q, r, engine) for q, r in zip(queries, responses)]


async def evaluate_agent(
    agent_name: str,
    test_queries: list[str] | None = None,
    system_prompt: str = "",
) -> dict[str, Any]:
    """
    Run test queries through an agent and score each response.
    Returns mean_score, mean_latency_ms, total_tokens_out alongside per-query details.
    Queries run concurrently (semaphore=3); responses are batch-scored in one LLM call.
    """
    from seraphim.agents.base import get_agent
    from seraphim.agents.core import AgentContext
    from seraphim.engine import get_engine

    queries = test_queries or _DEFAULT_TEST_QUERIES
    ag = get_agent(agent_name)
    if system_prompt:
        try:
            ag.system_prompt = system_prompt
        except AttributeError:
            pass  # @property with no setter — injected via context below

    engine = get_engine()
    sem = asyncio.Semaphore(3)

    async def _run_one(q: str) -> tuple[str, float]:
        async with sem:
            ctx = AgentContext()
            ctx.add_system(system_prompt if system_prompt else ag.system_prompt)
            t0 = time.monotonic()
            try:
                response = await ag.run(q, ctx)
                return response, (time.monotonic() - t0) * 1000
            except Exception as e:
                logger.warning("Eval query failed ('%s'): %s", q[:40], e)
                return "", (time.monotonic() - t0) * 1000

    query_results = await asyncio.gather(*[_run_one(q) for q in queries])
    responses = [r for r, _ in query_results]
    latencies = [lat for _, lat in query_results]

    # One batch judge call instead of N individual calls
    scores = await batch_score_responses(queries, responses, engine)

    for q, score, latency in zip(queries, scores, latencies):
        logger.info("  [eval] '%s...' → score=%.2f latency=%.0fms", q[:30], score, latency)

    tokens_out_total = sum(len(r) // 4 for r in responses)
    mean_score = sum(scores) / len(scores) if scores else 0.0
    mean_latency = sum(latencies) / len(latencies) if latencies else 0.0
    return {
        "mean_score": mean_score,
        "mean_latency_ms": mean_latency,
        "total_tokens_out": tokens_out_total,
        "scores": scores,
        "latencies_ms": latencies,
        "queries": queries,
    }

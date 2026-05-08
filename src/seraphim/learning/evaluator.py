"""LLM-as-judge evaluator — scores agent responses before/after optimization."""

from __future__ import annotations

import logging
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


async def evaluate_agent(
    agent_name: str,
    test_queries: list[str] | None = None,
    system_prompt: str = "",
) -> dict[str, Any]:
    """
    Run test queries through an agent and score each response.
    Returns mean_score, mean_latency_ms, total_tokens_out alongside per-query details.
    """
    from seraphim.agents.base import get_agent
    from seraphim.agents.core import AgentContext

    queries = test_queries or _DEFAULT_TEST_QUERIES
    ag = get_agent(agent_name)
    if system_prompt:
        try:
            ag.system_prompt = system_prompt
        except AttributeError:
            pass  # @property with no setter (e.g. ReActAgent) — injected via context below

    scores: list[float] = []
    latencies: list[float] = []
    tokens_out_total = 0

    for q in queries:
        ctx = AgentContext()
        ctx.add_system(system_prompt if system_prompt else ag.system_prompt)
        t0 = time.monotonic()
        try:
            response = await ag.run(q, ctx)
            latency_ms = (time.monotonic() - t0) * 1000
            score = await score_response(q, response)
            tokens_out_total += len(response) // 4
        except Exception as e:
            logger.warning("Eval query failed ('%s'): %s", q[:40], e)
            score = 0.0
            latency_ms = (time.monotonic() - t0) * 1000
        scores.append(score)
        latencies.append(latency_ms)
        logger.info("  [eval] '%s...' → score=%.2f latency=%.0fms", q[:30], score, latency_ms)

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

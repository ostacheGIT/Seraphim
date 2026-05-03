"""Prompt optimizer — few-shot injection from traces + optional DSPy."""

from __future__ import annotations

import logging
from typing import Any

from seraphim.learning.trace_store import load_sft_pairs, load_overlay

logger = logging.getLogger(__name__)

_MAX_EXAMPLES = 5
_MAX_EXAMPLE_LEN = 300


async def build_overlay(
    target: str,
    base_system_prompt: str,
    min_quality: float = 0.65,
    max_examples: int = _MAX_EXAMPLES,
) -> dict[str, Any]:
    """
    Build a prompt overlay for `target` (agent name).

    Returns a dict with:
    - system_prompt: updated system prompt with few-shot examples appended
    - examples: list of (query, response) pairs used
    - source: 'few_shot' or 'dspy'
    """
    pairs = await load_sft_pairs(agent=target, min_quality=min_quality, limit=max_examples * 3)

    # Deduplicate by instruction prefix (avoid very similar queries)
    seen: set[str] = set()
    examples = []
    for p in pairs:
        key = p["instruction"][:60]
        if key not in seen:
            seen.add(key)
            examples.append(p)
        if len(examples) >= max_examples:
            break

    if not examples:
        logger.info("No examples found for target '%s', overlay not built", target)
        return {}

    # Try DSPy first if available
    try:
        overlay = await _dspy_optimize(target, base_system_prompt, examples)
        overlay["source"] = "dspy"
        return overlay
    except ImportError:
        pass
    except Exception as e:
        logger.warning("DSPy optimization failed (%s), falling back to few-shot", e)

    # Few-shot: append examples to system prompt
    example_block = _format_examples(examples)
    overlay = {
        "system_prompt": base_system_prompt.rstrip() + "\n\n" + example_block,
        "examples": [{"query": _extract_query(p["instruction"]), "response": p["response"][:_MAX_EXAMPLE_LEN]} for p in examples],
        "source": "few_shot",
    }
    logger.info("Built few-shot overlay for '%s' with %d examples", target, len(examples))
    return overlay


def _format_examples(pairs: list[dict]) -> str:
    lines = ["--- Few-shot examples from past successful runs ---"]
    for i, p in enumerate(pairs, 1):
        query = _extract_query(p["instruction"])
        resp = p["response"][:_MAX_EXAMPLE_LEN]
        if len(p["response"]) > _MAX_EXAMPLE_LEN:
            resp += "..."
        lines.append(f"\nExample {i}:\nUser: {query}\nAssistant: {resp}")
    lines.append("--- End examples ---")
    return "\n".join(lines)


def _extract_query(instruction: str) -> str:
    for line in instruction.splitlines():
        if line.startswith("[USER]"):
            return line[7:].strip()
    return instruction[:80]


async def _dspy_optimize(target: str, base_prompt: str, examples: list[dict]) -> dict:
    import dspy  # type: ignore[import]

    class QA(dspy.Signature):
        """Answer the user's question helpfully and concisely."""
        question: str = dspy.InputField()
        answer: str = dspy.OutputField()

    trainset = [
        dspy.Example(
            question=_extract_query(p["instruction"]),
            answer=p["response"][:_MAX_EXAMPLE_LEN],
        ).with_inputs("question")
        for p in examples
    ]

    # Use BootstrapFewShot for prompt optimization
    from dspy.teleprompt import BootstrapFewShot  # type: ignore[import]

    def metric(ex, pred, _trace=None):
        return len(pred.answer) > 20

    teleprompter = BootstrapFewShot(metric=metric, max_bootstrapped_demos=3)
    program = dspy.Predict(QA)
    compiled = teleprompter.compile(program, trainset=trainset)

    # Extract few-shot demos from compiled program
    demos = getattr(compiled, "demos", []) or []
    demo_examples = [{"question": d.get("question", ""), "answer": d.get("answer", "")} for d in demos]

    return {
        "system_prompt": base_prompt,
        "dspy_demos": demo_examples,
        "examples": demo_examples,
    }


async def get_active_system_prompt(agent_name: str, base_prompt: str) -> str:
    """Return the active system prompt: overlay if accepted, else base."""
    overlay = await load_overlay(agent_name)
    if overlay and "system_prompt" in overlay:
        return overlay["system_prompt"]
    return base_prompt

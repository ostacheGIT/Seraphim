"""
Learning orchestrator — full loop:
  collect traces → mine SFT pairs → optimize prompts → eval → accept/reject
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from seraphim.learning.trace_store import trace_stats, save_overlay
from seraphim.learning.miner import mine
from seraphim.learning.optimizer import build_overlay
from seraphim.learning.evaluator import evaluate_agent

logger = logging.getLogger(__name__)


@dataclass
class LearningConfig:
    agents: list[str] = field(default_factory=lambda: ["react", "chat"])
    min_traces: int = 5               # minimum traces before optimizing
    min_quality: float = 0.6          # quality threshold for SFT mining
    min_improvement: float = 0.05     # accept overlay only if score improves by this
    max_examples: int = 5             # few-shot examples per overlay
    test_queries: list[str] | None = None
    dry_run: bool = False             # if True, build overlays but don't save
    run_grpo: bool = False            # Step 1b: GRPO sampling before SFT mining
    grpo_generations: int = 4         # N responses per prompt for GRPO
    grpo_max_prompts: int = 30        # max prompts per GRPO run
    grpo_backprop: bool = False       # enable HuggingFace GRPO backprop (requires torch)
    run_distill: bool = False         # Step 1c: knowledge distillation from teacher model
    distill_teacher_type: str = "ollama"
    distill_teacher_model: str = "qwen2.5:14b"
    distill_threshold: float = 0.5
    run_finetune: bool = False        # Step 3: LoRA fine-tune after mining
    finetune_base_model: str = "Qwen/Qwen2.5-3B-Instruct"
    finetune_epochs: int = 3
    finetune_ollama_name: str = "seraphim-tuned"


@dataclass
class LearningResult:
    stats_before: dict[str, Any]
    mined_pairs: int
    overlays: list[dict[str, Any]]
    accepted: int
    rejected: int
    grpo_result: dict[str, Any] | None = None
    distill_result: dict[str, Any] | None = None
    finetune_result: dict[str, Any] | None = None


async def _optimize_single_agent(
    agent_name: str,
    config: "LearningConfig",
) -> "dict[str, Any] | None":
    """Optimize one agent: build overlay + eval-before in parallel, then eval-after."""
    logger.info("Optimizing agent '%s'...", agent_name)

    try:
        from seraphim.agents.base import get_agent
        ag = get_agent(agent_name)
        base_prompt = ag.system_prompt
    except Exception as e:
        logger.warning("Could not load agent '%s': %s", agent_name, e)
        return None

    from seraphim.learning.trace_store import load_sft_pairs
    pairs = await load_sft_pairs(agent=agent_name, min_quality=config.min_quality, limit=config.min_traces)
    if len(pairs) < config.min_traces:
        logger.info("Agent '%s': only %d pairs (need %d), skipping", agent_name, len(pairs), config.min_traces)
        return None

    # Build overlay and run BEFORE eval concurrently — they're independent
    logger.info("Building overlay + evaluating '%s' BEFORE (parallel)...", agent_name)
    overlay, before = await asyncio.gather(
        build_overlay(
            target=agent_name,
            base_system_prompt=base_prompt,
            min_quality=config.min_quality,
            max_examples=config.max_examples,
        ),
        evaluate_agent(agent_name, config.test_queries, base_prompt),
    )

    if not overlay:
        logger.info("Agent '%s': no overlay built", agent_name)
        return None

    logger.info("Evaluating '%s' AFTER optimization...", agent_name)
    after = await evaluate_agent(
        agent_name, config.test_queries, overlay.get("system_prompt", base_prompt)
    )

    improvement = after["mean_score"] - before["mean_score"]
    latency_delta = after["mean_latency_ms"] - before["mean_latency_ms"]
    accept = improvement >= config.min_improvement

    logger.info(
        "Agent '%s': score %.3f→%.3f (Δ%.3f)  latency %.0f→%.0fms (Δ%+.0f)  tokens_out=%d → %s",
        agent_name,
        before["mean_score"], after["mean_score"], improvement,
        before["mean_latency_ms"], after["mean_latency_ms"], latency_delta,
        after["total_tokens_out"],
        "ACCEPT" if accept else "REJECT",
    )

    if not config.dry_run:
        await save_overlay(
            target=agent_name,
            overlay=overlay,
            score_before=before["mean_score"],
            score_after=after["mean_score"],
            accepted=accept,
            latency_before_ms=before["mean_latency_ms"],
            latency_after_ms=after["mean_latency_ms"],
        )

    return {
        "agent": agent_name,
        "overlay": overlay,
        "score_before": before["mean_score"],
        "score_after": after["mean_score"],
        "improvement": improvement,
        "latency_before_ms": before["mean_latency_ms"],
        "latency_after_ms": after["mean_latency_ms"],
        "tokens_out": after["total_tokens_out"],
        "accepted": accept,
    }


async def run_learning_loop(config: LearningConfig | None = None) -> LearningResult:
    if config is None:
        config = LearningConfig()

    stats_before = await trace_stats()
    logger.info("=== Learning loop start === traces=%d good=%d",
                stats_before["total_traces"], stats_before["good_traces"])

    # ── Step 0: GRPO sampling (optional) — runs before SFT mining ───────────
    grpo_result: dict[str, Any] | None = None
    if config.run_grpo and not config.dry_run:
        from seraphim.learning.grpo_trainer import GRPOConfig, run_grpo
        grpo_cfg = GRPOConfig(
            num_generations=config.grpo_generations,
            min_prompts=1,
            max_prompts=config.grpo_max_prompts,
            run_backprop=config.grpo_backprop,
        )
        logger.info("=== GRPO sampling start === generations=%d max_prompts=%d",
                    grpo_cfg.num_generations, grpo_cfg.max_prompts)
        gr = await run_grpo(grpo_cfg)
        grpo_result = {
            "success": gr.success,
            "prompts_used": gr.prompts_used,
            "total_generations": gr.total_generations,
            "mean_reward": gr.mean_reward,
            "pairs_saved": gr.pairs_saved,
            "backprop_done": gr.backprop_done,
            "message": gr.message,
        }
        logger.info(
            "=== GRPO done === prompts=%d generations=%d mean_reward=%.3f pairs_saved=%d",
            gr.prompts_used, gr.total_generations, gr.mean_reward, gr.pairs_saved,
        )

    # ── Step 0b: Knowledge distillation (optional) ──────────────────────────
    distill_result: dict[str, Any] | None = None
    if config.run_distill and not config.dry_run:
        from seraphim.learning.distiller import DistillConfig, run_distillation
        dist_cfg = DistillConfig(
            teacher_type=config.distill_teacher_type,
            teacher_model=config.distill_teacher_model,
            min_score_threshold=config.distill_threshold,
        )
        logger.info("=== Distillation start === teacher=%s/%s",
                    config.distill_teacher_type, config.distill_teacher_model)
        dr = await run_distillation(dist_cfg)
        distill_result = {
            "success": dr.success,
            "weak_classes": dr.weak_classes,
            "queries_processed": dr.queries_processed,
            "pairs_saved": dr.pairs_saved,
            "message": dr.message,
        }
        logger.info("=== Distillation done === pairs=%d weak=%s",
                    dr.pairs_saved, dr.weak_classes)

    # ── Step 1: Mine SFT pairs (all agents in parallel) ─────────────────────
    mine_counts = await asyncio.gather(*[
        mine(agent=agent_name, min_quality=config.min_quality)
        for agent_name in config.agents
    ])
    total_mined = sum(mine_counts)
    for agent_name, n in zip(config.agents, mine_counts):
        logger.info("Mined %d pairs for agent '%s'", n, agent_name)

    # ── Step 2: Optimize + Eval + Accept/Reject (all agents in parallel) ────
    opt_results = await asyncio.gather(*[
        _optimize_single_agent(agent_name, config)
        for agent_name in config.agents
    ])

    overlays_built: list[dict[str, Any]] = [r for r in opt_results if r is not None]
    accepted = sum(1 for r in overlays_built if r["accepted"])
    rejected = len(overlays_built) - accepted

    # ── Step 3: LoRA fine-tuning (optional) ─────────────────────────────────
    finetune_result: dict[str, Any] | None = None
    if config.run_finetune and not config.dry_run:
        from seraphim.learning.finetuner import FineTuneConfig, run_lora_finetune
        from seraphim.learning.miner import export_jsonl

        sft_path = str(Path.home() / ".seraphim" / "sft_pairs.jsonl")
        await export_jsonl(sft_path, min_quality=config.min_quality)

        ft_cfg = FineTuneConfig(
            base_model=config.finetune_base_model,
            epochs=config.finetune_epochs,
            sft_path=sft_path,
            ollama_model_name=config.finetune_ollama_name,
        )
        logger.info("=== LoRA fine-tuning start ===")
        ft = await run_lora_finetune(ft_cfg)
        finetune_result = {
            "success": ft.success,
            "train_loss": ft.train_loss,
            "ollama_model": ft.ollama_model,
            "message": ft.message,
        }
        logger.info("=== LoRA fine-tuning done === success=%s loss=%s", ft.success, ft.train_loss)

    logger.info("=== Learning loop done === mined=%d accepted=%d rejected=%d",
                total_mined, accepted, rejected)

    return LearningResult(
        stats_before=stats_before,
        mined_pairs=total_mined,
        overlays=overlays_built,
        accepted=accepted,
        rejected=rejected,
        grpo_result=grpo_result,
        distill_result=distill_result,
        finetune_result=finetune_result,
    )

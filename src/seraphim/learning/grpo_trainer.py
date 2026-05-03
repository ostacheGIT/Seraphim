"""GRPO (Group Relative Policy Optimization) trainer.

Two-phase approach:
  Phase 1 — Sampling (always runs, no GPU needed):
    For each prompt from the trace store, sample N responses via Ollama,
    score each with LLM-as-judge, compute group-relative advantages, and
    store above-threshold winners as high-quality SFT pairs.

  Phase 2 — Backprop (optional, requires torch + trl >= 0.9):
    Full GRPO policy-gradient update on a local HuggingFace model using
    the precomputed reward function (Ollama judge, called synchronously).
    Saves a LoRA adapter to output_dir.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_BACKPROP_DEPS = ["torch", "transformers", "peft", "trl", "datasets"]


# ── Config & Result ───────────────────────────────────────────────────────────

@dataclass
class GRPOConfig:
    # Phase 1 — sampling
    num_generations: int = 4        # N responses per prompt
    min_prompts: int = 5            # abort if fewer unique prompts available
    max_prompts: int = 50           # cap prompts per run (cost control)
    agent: str = ""                 # filter traces by agent ("" = all)
    advantage_threshold: float = 0.0  # save pairs with advantage > this

    # Phase 2 — backprop (disabled by default)
    run_backprop: bool = False
    base_model: str = "Qwen/Qwen2.5-3B-Instruct"
    output_dir: str = "~/.seraphim/grpo_adapter"
    epochs: int = 1
    lora_r: int = 16
    lora_alpha: int = 32
    learning_rate: float = 5e-7
    kl_coeff: float = 0.04
    clip_epsilon: float = 0.2
    max_new_tokens: int = 256
    max_prompt_length: int = 256
    ollama_judge_model: str = "qwen2.5:3b"  # model used as reward judge
    use_4bit: bool = True                   # 4-bit quantization (fits 4GB VRAM)


@dataclass
class GRPOResult:
    success: bool
    prompts_used: int
    total_generations: int
    mean_reward: float
    mean_advantage_saved: float
    pairs_saved: int
    backprop_done: bool = False
    output_dir: str = ""
    message: str = ""


# ── Pure helpers ──────────────────────────────────────────────────────────────

def group_advantages(rewards: list[float]) -> list[float]:
    """Compute group-relative advantages: A_i = (r_i - mean) / (std + ε)."""
    n = len(rewards)
    if n == 0:
        return []
    mean = sum(rewards) / n
    variance = sum((r - mean) ** 2 for r in rewards) / n
    std = variance ** 0.5
    return [(r - mean) / (std + 1e-8) for r in rewards]


def check_backprop_deps() -> list[str]:
    missing = []
    for pkg in _BACKPROP_DEPS:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    return missing


# ── Phase 1 — Ollama sampling ─────────────────────────────────────────────────

async def _sample_group(
    query: str,
    engine: Any,
    n: int,
    score_fn: Any,
) -> list[tuple[str, float]]:
    """Generate N responses for query and score each. Returns [(response, reward)]."""
    tasks = []
    for _ in range(n):
        tasks.append(_generate_and_score(query, engine, score_fn))
    return await asyncio.gather(*tasks)


async def _generate_and_score(query: str, engine: Any, score_fn: Any) -> tuple[str, float]:
    try:
        result = await engine.chat([{"role": "user", "content": query}])
        msgs = result.get("messages", []) if isinstance(result, dict) else getattr(result, "messages", [])
        response = msgs[-1].get("content", "") if msgs else ""
    except Exception as exc:
        logger.warning("Generate failed for '%s…': %s", query[:40], exc)
        return "", 0.0
    reward = await score_fn(query, response, engine)
    return response, reward


async def run_grpo_sampling(config: GRPOConfig) -> GRPOResult:
    """Phase 1: sample N responses per prompt, score, store winners as SFT pairs."""
    from seraphim.learning.trace_store import load_traces, save_sft_pair
    from seraphim.learning.evaluator import score_response
    from seraphim.engine import get_engine

    traces = await load_traces(
        agent=config.agent or None,
        limit=config.max_prompts * 3,  # over-fetch to account for short/dup queries
    )

    # Deduplicate by query text (keep unique, non-trivial prompts)
    seen_queries: set[str] = set()
    prompts: list[tuple[str, str]] = []  # (query, agent)
    for t in traces:
        q = t.query.strip()
        if len(q) < 10 or q in seen_queries:
            continue
        seen_queries.add(q)
        prompts.append((q, t.agent))
        if len(prompts) >= config.max_prompts:
            break

    if len(prompts) < config.min_prompts:
        return GRPOResult(
            success=False,
            prompts_used=len(prompts),
            total_generations=0,
            mean_reward=0.0,
            mean_advantage_saved=0.0,
            pairs_saved=0,
            message=f"Not enough prompts: {len(prompts)} < {config.min_prompts}. "
                    "Accumulate more traces first.",
        )

    engine = get_engine()
    all_rewards: list[float] = []
    saved_advantages: list[float] = []
    pairs_saved = 0

    for i, (query, agent) in enumerate(prompts):
        logger.info("[GRPO] prompt %d/%d: '%s…'", i + 1, len(prompts), query[:50])

        group = await _sample_group(query, engine, config.num_generations, score_response)
        rewards = [r for _, r in group]
        advantages = group_advantages(rewards)
        all_rewards.extend(rewards)

        logger.debug(
            "[GRPO]   rewards=%s  advantages=%s",
            [f"{r:.2f}" for r in rewards],
            [f"{a:+.2f}" for a in advantages],
        )

        # Store above-threshold pairs as high-quality SFT data
        for (response, reward), advantage in zip(group, advantages):
            if not response or advantage <= config.advantage_threshold:
                continue
            # Map advantage to quality score in [0.5, 1.0]
            quality = 0.5 + 0.5 * min(1.0, max(0.0, (advantage + 2.0) / 4.0))
            trace_id = f"grpo_{uuid.uuid4().hex[:12]}"
            inserted = await save_sft_pair(
                trace_id=trace_id,
                agent=agent,
                instruction=query,
                response=response,
                quality=quality,
            )
            if inserted:
                pairs_saved += 1
                saved_advantages.append(advantage)

    mean_reward = sum(all_rewards) / len(all_rewards) if all_rewards else 0.0
    mean_adv = sum(saved_advantages) / len(saved_advantages) if saved_advantages else 0.0

    logger.info(
        "[GRPO] Sampling done. prompts=%d generations=%d mean_reward=%.3f pairs_saved=%d",
        len(prompts), len(all_rewards), mean_reward, pairs_saved,
    )

    return GRPOResult(
        success=True,
        prompts_used=len(prompts),
        total_generations=len(all_rewards),
        mean_reward=mean_reward,
        mean_advantage_saved=mean_adv,
        pairs_saved=pairs_saved,
        message=f"Phase 1 done — {pairs_saved} new pairs (mean_reward={mean_reward:.3f})",
    )


# ── Phase 2 — HuggingFace GRPO backprop ──────────────────────────────────────

def _sync_ollama_reward(
    prompts: list[str],
    completions: list[Any],
    judge_model: str = "qwen2.5:3b",
    **kwargs: Any,
) -> list[float]:
    """Reward function for TRL GRPOTrainer — calls Ollama judge synchronously."""
    import re
    import requests

    _JUDGE_SYS = (
        "You are a strict evaluator. "
        "Rate the response quality on a scale of 1 to 5. "
        "Reply with ONLY a single digit."
    )
    rewards = []
    for prompt, completion in zip(prompts, completions):
        # TRL may pass completion as dict {"content": str} or raw str
        text = completion if isinstance(completion, str) else completion.get("content", "")
        try:
            resp = requests.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": judge_model,
                    "messages": [
                        {"role": "system", "content": _JUDGE_SYS},
                        {
                            "role": "user",
                            "content": f"Question: {prompt[:300]}\n\nResponse: {text[:600]}",
                        },
                    ],
                    "stream": False,
                },
                timeout=30,
            )
            raw = resp.json().get("message", {}).get("content", "3").strip()
            m = re.search(r"[1-5]", raw)
            score = int(m.group()) if m else 3
        except Exception as exc:
            logger.debug("Reward call failed: %s", exc)
            score = 3
        rewards.append(score / 5.0)
    return rewards


def _run_backprop_sync(config: GRPOConfig, prompts: list[str]) -> dict[str, Any]:
    """Run GRPO policy-gradient training. Called in thread executor."""
    missing = check_backprop_deps()
    if missing:
        return {
            "success": False,
            "message": f"Missing deps: {', '.join(missing)}. "
                       f"Install: pip install {' '.join(missing)}",
        }

    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from peft import LoraConfig, TaskType
        from datasets import Dataset

        # TRL GRPOTrainer was added in trl 0.9.0
        try:
            from trl import GRPOTrainer
            from trl import GRPOConfig as TRLGRPOConfig
        except ImportError:
            return {
                "success": False,
                "message": "trl >= 0.9.0 required for GRPOTrainer. "
                           "Run: pip install --upgrade trl",
            }

        has_cuda = torch.cuda.is_available()
        # Ampere+ (RTX 30xx/40xx, A100…) support BF16 natively — prefer it over FP16
        # because Qwen2.5 weights are BF16 by default and FP16 scaler can't handle them.
        has_bf16 = has_cuda and torch.cuda.is_bf16_supported()
        compute_dtype = torch.bfloat16 if has_bf16 else (torch.float16 if has_cuda else torch.float32)

        output_dir = Path(config.output_dir).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)

        # Try 4-bit quantization on GPU to fit in limited VRAM (e.g. 4GB RTX 3050)
        use_4bit = has_cuda
        bnb_config = None
        if use_4bit:
            try:
                from transformers import BitsAndBytesConfig
                bnb_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=compute_dtype,
                    bnb_4bit_use_double_quant=True,
                )
            except Exception:
                use_4bit = False

        logger.info(
            "[GRPO] Loading model %s (cuda=%s, bf16=%s, 4bit=%s)",
            config.base_model, has_cuda, has_bf16, use_4bit,
        )
        tokenizer = AutoTokenizer.from_pretrained(
            config.base_model, trust_remote_code=True
        )
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        load_kwargs: dict = {"trust_remote_code": True}
        if use_4bit and bnb_config:
            load_kwargs["quantization_config"] = bnb_config
            load_kwargs["device_map"] = "auto"
        elif has_cuda:
            load_kwargs["torch_dtype"] = compute_dtype
            load_kwargs["device_map"] = "auto"
        else:
            load_kwargs["torch_dtype"] = torch.float32

        model = AutoModelForCausalLM.from_pretrained(config.base_model, **load_kwargs)

        lora_cfg = LoraConfig(
            r=config.lora_r,
            lora_alpha=config.lora_alpha,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
            task_type=TaskType.CAUSAL_LM,
            bias="none",
        )

        dataset = Dataset.from_list([{"prompt": p} for p in prompts])

        judge_model = config.ollama_judge_model

        def reward_fn(prompts, completions, **kw):
            return _sync_ollama_reward(prompts, completions, judge_model)

        grpo_args = TRLGRPOConfig(
            output_dir=str(output_dir),
            num_train_epochs=config.epochs,
            per_device_train_batch_size=1,
            learning_rate=config.learning_rate,
            num_generations=config.num_generations,
            generation_batch_size=config.num_generations,  # must be divisible by num_generations
            max_completion_length=config.max_new_tokens,   # trl 1.x
            beta=config.kl_coeff,                          # trl 1.x (was kl_coeff)
            epsilon=config.clip_epsilon,                   # trl 1.x (was clip_epsilon)
            logging_steps=5,
            save_strategy="no",
            fp16=has_cuda and not has_bf16,
            bf16=has_bf16,
            use_cpu=not has_cuda,
            report_to="none",
        )

        from transformers import TrainerCallback, TrainerState, TrainerControl, TrainingArguments
        from seraphim.engine.metrics import get_gpu_snapshot, TrainingStepMetrics

        step_metrics: list[dict] = []
        metrics_log_path = output_dir / "training_metrics.jsonl"

        class GpuMetricsCallback(TrainerCallback):
            def on_log(self, args: TrainingArguments, state: TrainerState,
                       control: TrainerControl, logs=None, **kw):
                if not logs:
                    return
                gpu = get_gpu_snapshot()
                m = TrainingStepMetrics(
                    step=state.global_step,
                    loss=float(logs.get("loss", 0)),
                    grad_norm=float(logs.get("grad_norm", 0)),
                    learning_rate=float(logs.get("learning_rate", 0)),
                    reward_mean=float(logs.get("reward", 0)),
                    reward_std=float(logs.get("reward_std", 0)),
                    kl=float(logs.get("kl", 0)),
                    gpu=gpu,
                )
                d = m.to_dict()
                step_metrics.append(d)
                logger.info(
                    "[GRPO step %d] loss=%.4f reward=%.3f kl=%.5f"
                    " gpu=%s%% vram=%.0fMB",
                    state.global_step, m.loss, m.reward_mean, m.kl,
                    f"{gpu.gpu_util_pct:.0f}" if gpu else "?",
                    gpu.vram_used_mb if gpu else 0,
                )
                # Append to JSONL log
                try:
                    import json
                    with open(metrics_log_path, "a") as f:
                        f.write(json.dumps(d) + "\n")
                except Exception:
                    pass

        trainer = GRPOTrainer(
            model=model,
            reward_funcs=reward_fn,
            args=grpo_args,
            train_dataset=dataset,
            peft_config=lora_cfg,
            processing_class=tokenizer,
            callbacks=[GpuMetricsCallback()],
        )

        logger.info("[GRPO] Starting backprop on %d prompts...", len(prompts))
        trainer.train()
        trainer.save_model(str(output_dir))
        tokenizer.save_pretrained(str(output_dir))
        logger.info("[GRPO] Backprop done. Adapter saved to %s", output_dir)

        return {"success": True, "output_dir": str(output_dir), "step_metrics": step_metrics}

    except Exception as exc:
        logger.exception("[GRPO] Backprop failed")
        return {"success": False, "message": f"Backprop failed: {exc or type(exc).__name__}"}


# ── Public entry point ────────────────────────────────────────────────────────

async def run_grpo(config: GRPOConfig | None = None) -> GRPOResult:
    """Run GRPO: Phase 1 (sampling always) + Phase 2 (backprop if enabled)."""
    if config is None:
        config = GRPOConfig()

    result = await run_grpo_sampling(config)

    if result.success and config.run_backprop and result.prompts_used > 0:
        from seraphim.learning.trace_store import load_traces
        traces = await load_traces(agent=config.agent or None, limit=config.max_prompts)
        prompts = list({t.query.strip() for t in traces if len(t.query.strip()) > 10})

        loop = asyncio.get_event_loop()
        bp = await loop.run_in_executor(None, _run_backprop_sync, config, prompts)

        result.backprop_done = bp.get("success", False)
        result.output_dir = bp.get("output_dir", "")
        suffix = (
            f" Adapter: {result.output_dir}"
            if result.backprop_done
            else f" Backprop failed: {bp.get('message', '')}"
        )
        result.message += suffix

    return result


__all__ = [
    "GRPOConfig",
    "GRPOResult",
    "run_grpo",
    "run_grpo_sampling",
    "group_advantages",
    "check_backprop_deps",
]

"""GPU and inference metrics collection.

InferenceMetrics captures per-call stats extracted from Ollama response fields
(prompt_eval_duration, eval_count, eval_duration, total_duration) plus a GPU
snapshot (utilization %, VRAM) taken via pynvml → nvidia-smi → torch.cuda fallback.
"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── GPU snapshot ──────────────────────────────────────────────────────────────

@dataclass
class GpuSnapshot:
    gpu_util_pct: float = 0.0       # 0–100
    vram_used_mb: float = 0.0
    vram_total_mb: float = 0.0
    vram_free_mb: float = 0.0
    gpu_name: str = ""

    @property
    def vram_used_pct(self) -> float:
        return (self.vram_used_mb / self.vram_total_mb * 100) if self.vram_total_mb else 0.0


def get_gpu_snapshot(device_index: int = 0) -> GpuSnapshot | None:
    """Return a GPU snapshot. Tries pynvml → nvidia-smi → torch.cuda → None."""
    # 1 — pynvml (fastest, no subprocess)
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(device_index)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(name, bytes):
            name = name.decode()
        return GpuSnapshot(
            gpu_util_pct=float(util.gpu),
            vram_used_mb=mem.used / 1024**2,
            vram_total_mb=mem.total / 1024**2,
            vram_free_mb=mem.free / 1024**2,
            gpu_name=name,
        )
    except Exception:
        pass

    # 2 — nvidia-smi subprocess
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            timeout=3,
            text=True,
            stderr=subprocess.DEVNULL,
        )
        lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
        if device_index < len(lines):
            parts = [p.strip() for p in lines[device_index].split(",")]
            if len(parts) >= 5:
                return GpuSnapshot(
                    gpu_name=parts[0],
                    gpu_util_pct=float(parts[1]),
                    vram_used_mb=float(parts[2]),
                    vram_total_mb=float(parts[3]),
                    vram_free_mb=float(parts[4]),
                )
    except Exception:
        pass

    # 3 — torch.cuda (VRAM only, no utilization)
    try:
        import torch
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(device_index)
            used = torch.cuda.memory_allocated(device_index)
            total = props.total_memory
            return GpuSnapshot(
                gpu_name=props.name,
                gpu_util_pct=0.0,
                vram_used_mb=used / 1024**2,
                vram_total_mb=total / 1024**2,
                vram_free_mb=(total - used) / 1024**2,
            )
    except Exception:
        pass

    return None


# ── Per-inference metrics ─────────────────────────────────────────────────────

@dataclass
class InferenceMetrics:
    """Metrics for a single LLM inference call."""
    # Timing
    ttft_ms: float = 0.0          # time to first token (prompt eval time)
    total_latency_ms: float = 0.0 # end-to-end wall-clock time
    # Throughput
    tokens_in: int = 0
    tokens_out: int = 0
    throughput_tps: float = 0.0   # output tokens per second
    # GPU (snapshot taken right after inference)
    gpu: GpuSnapshot | None = None

    @property
    def gpu_util_pct(self) -> float:
        return self.gpu.gpu_util_pct if self.gpu else 0.0

    @property
    def vram_used_mb(self) -> float:
        return self.gpu.vram_used_mb if self.gpu else 0.0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ttft_ms": round(self.ttft_ms, 2),
            "total_latency_ms": round(self.total_latency_ms, 2),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "throughput_tps": round(self.throughput_tps, 2),
            "gpu_util_pct": round(self.gpu_util_pct, 1),
            "vram_used_mb": round(self.vram_used_mb, 1),
        }
        if self.gpu:
            d["gpu_name"] = self.gpu.gpu_name
            d["vram_total_mb"] = round(self.gpu.vram_total_mb, 1)
        return d


def parse_ollama_metrics(data: dict[str, Any], wall_start_ns: int) -> InferenceMetrics:
    """
    Extract InferenceMetrics from an Ollama /api/generate response dict.

    Ollama timing fields (all in nanoseconds):
      prompt_eval_duration  — time to evaluate the prompt (≈ TTFT)
      eval_duration         — time to generate output tokens
      total_duration        — full request time
      prompt_eval_count     — prompt token count
      eval_count            — output token count
    """
    wall_total_ms = (time.perf_counter_ns() - wall_start_ns) / 1e6

    prompt_eval_ns = data.get("prompt_eval_duration", 0) or 0
    eval_ns = data.get("eval_duration", 0) or 0
    total_ns = data.get("total_duration", 0) or 0

    tokens_in = data.get("prompt_eval_count", 0) or 0
    tokens_out = data.get("eval_count", 0) or 0

    ttft_ms = prompt_eval_ns / 1e6
    total_ms = (total_ns / 1e6) if total_ns else wall_total_ms
    throughput = tokens_out / (eval_ns / 1e9) if eval_ns > 0 else 0.0

    gpu = get_gpu_snapshot()
    return InferenceMetrics(
        ttft_ms=ttft_ms,
        total_latency_ms=total_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        throughput_tps=throughput,
        gpu=gpu,
    )


# ── Training step metrics ─────────────────────────────────────────────────────

@dataclass
class TrainingStepMetrics:
    step: int
    loss: float
    grad_norm: float
    learning_rate: float
    reward_mean: float = 0.0
    reward_std: float = 0.0
    kl: float = 0.0
    gpu: GpuSnapshot | None = None
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "step": self.step,
            "loss": round(self.loss, 6),
            "grad_norm": round(self.grad_norm, 4),
            "lr": self.learning_rate,
            "reward_mean": round(self.reward_mean, 4),
            "reward_std": round(self.reward_std, 4),
            "kl": round(self.kl, 6),
            "ts": self.timestamp_ms,
        }
        if self.gpu:
            d["gpu_util_pct"] = round(self.gpu.gpu_util_pct, 1)
            d["vram_used_mb"] = round(self.gpu.vram_used_mb, 1)
        return d


__all__ = [
    "GpuSnapshot",
    "InferenceMetrics",
    "TrainingStepMetrics",
    "get_gpu_snapshot",
    "parse_ollama_metrics",
]

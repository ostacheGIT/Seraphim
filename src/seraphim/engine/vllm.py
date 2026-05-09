from __future__ import annotations

import json
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from seraphim.engine.base import ChatMessage, ChatResult, LLMEngine
from seraphim.engine.metrics import InferenceMetrics, get_gpu_snapshot


# Models known to fit in 4GB VRAM (3050 Ti) with 4-bit quantization:
#   "Qwen/Qwen2.5-3B-Instruct-GPTQ-Int4"   (~1.8 GB) ← recommended default
#   "microsoft/phi-3.5-mini-instruct"        (~2.2 GB, AWQ or GPTQ)
#   "meta-llama/Llama-3.2-3B-Instruct"      (~1.9 GB in 4-bit)
#
# Start the server (4GB VRAM profile):
#   vllm serve Qwen/Qwen2.5-3B-Instruct-GPTQ-Int4 \
#       --gpu-memory-utilization 0.85 \
#       --max-model-len 4096 \
#       --port 8000


class VLLMEngine:
    """
    LLMEngine backed by a vLLM OpenAI-compatible server.

    vLLM exposes /v1/chat/completions, /v1/models, and /health.
    Tool calls are normalized to match the Ollama internal format
    (arguments as dict, not JSON string).
    """

    id = "vllm"
    name = "vLLM"

    def __init__(
        self,
        model: str = "Qwen/Qwen2.5-3B-Instruct-GPTQ-Int4",
        base_url: str = "http://localhost:8000",
        timeout: float = 120.0,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.last_metrics: InferenceMetrics | None = None

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=5.0) as client:
                r = await client.get("/health")
                return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> List[str]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10.0) as client:
            r = await client.get("/v1/models")
            r.raise_for_status()
            data = r.json()
        return [m["id"] for m in data.get("data", [])]

    async def chat(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ChatResult:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": kwargs.pop("temperature", self.temperature),
            "max_tokens": kwargs.pop("max_tokens", self.max_tokens),
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if kwargs:
            payload.update(kwargs)

        t0 = time.perf_counter_ns()
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            r = await client.post("/v1/chat/completions", json=payload)
            r.raise_for_status()
            data = r.json()
        wall_ms = (time.perf_counter_ns() - t0) / 1e6

        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        content = msg.get("content") or ""
        raw_tool_calls = msg.get("tool_calls") or []

        result_msg: ChatMessage = {"role": "assistant", "content": content}
        if raw_tool_calls:
            result_msg["tool_calls"] = _normalize_tool_calls(raw_tool_calls)

        usage = data.get("usage", {})
        tokens_in = usage.get("prompt_tokens", 0)
        tokens_out = usage.get("completion_tokens", 0)
        tps = tokens_out / (wall_ms / 1000) if wall_ms > 0 else 0.0

        self.last_metrics = InferenceMetrics(
            ttft_ms=wall_ms,
            total_latency_ms=wall_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            throughput_tps=round(tps, 2),
            gpu=get_gpu_snapshot(),
        )

        return ChatResult(
            messages=[result_msg],
            usage=usage,
            metrics=self.last_metrics.to_dict(),
        )

    async def stream_chat(
        self,
        messages: List[ChatMessage],
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": kwargs.pop("temperature", self.temperature),
            "max_tokens": kwargs.pop("max_tokens", self.max_tokens),
            "stream": True,
        }
        if kwargs:
            payload.update(kwargs)

        t0 = time.perf_counter_ns()
        first_token_ns: int | None = None
        tokens_out = 0

        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        data = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    token = delta.get("content") or ""
                    if token:
                        if first_token_ns is None:
                            first_token_ns = time.perf_counter_ns()
                        tokens_out += 1
                        yield token

        wall_ms = (time.perf_counter_ns() - t0) / 1e6
        ttft_ms = (first_token_ns - t0) / 1e6 if first_token_ns else wall_ms
        tps = tokens_out / (wall_ms / 1000) if wall_ms > 0 else 0.0
        self.last_metrics = InferenceMetrics(
            ttft_ms=ttft_ms,
            total_latency_ms=wall_ms,
            tokens_in=0,
            tokens_out=tokens_out,
            throughput_tps=round(tps, 2),
            gpu=get_gpu_snapshot(),
        )


def _normalize_tool_calls(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OpenAI tool_calls (arguments as JSON string) → internal format (arguments as dict)."""
    normalized = []
    for tc in raw:
        fn = tc.get("function", {})
        args = fn.get("arguments", "{}")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        normalized.append({
            "id": tc.get("id", ""),
            "type": tc.get("type", "function"),
            "function": {
                "name": fn.get("name", ""),
                "arguments": args,
            },
        })
    return normalized

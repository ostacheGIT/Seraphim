from __future__ import annotations

import logging
from typing import Dict, Optional

from seraphim.engine.base import LLMEngine
from seraphim.engine.ollama import OllamaEngine

_engines: Dict[str, LLMEngine] = {}
_default_engine_id: Optional[str] = None
_initialized: bool = False
_gpu_available: bool = False

logger = logging.getLogger(__name__)


def gpu_available() -> bool:
    _ensure_initialized()
    return _gpu_available


def _ensure_initialized() -> None:
    global _initialized
    if not _initialized:
        init_engines()
        _initialized = True


def init_engines() -> None:
    global _default_engine_id, _gpu_available

    from seraphim.engine.metrics import get_gpu_snapshot
    _gpu = get_gpu_snapshot()
    _gpu_available = _gpu is not None
    if _gpu_available:
        logger.info("GPU detected: %s (%.0f MB free)", _gpu.gpu_name, _gpu.vram_free_mb)
    else:
        logger.info("No GPU detected — running on CPU")

    try:
        from seraphim.settings import settings
        provider = settings.engine.provider
        model = settings.engine.model
        base_url = settings.engine.base_url
        temperature = settings.engine.temperature
    except Exception:
        provider = "ollama"
        model = "qwen2.5:3b"
        base_url = "http://localhost:11434"
        temperature = 0.7

    # Always register Ollama engines as fallback
    register_engine("ollama_qwen3b", OllamaEngine(model="qwen2.5:3b", base_url=base_url), default=(provider == "ollama"))
    register_engine("ollama_qwen7b", OllamaEngine(
        model="qwen2.5:7b-instruct-q2_k",  # 3.0GB — fits in 4GB VRAM (q4_k_m 4.7GB did not)
        base_url=base_url,
        timeout=300.0,
        options={"num_ctx": 2048},  # KV cache ~120MB at 2048 → total ~3.1GB, fits in VRAM
    ), default=False)

    if provider == "vllm":
        from seraphim.engine.vllm import VLLMEngine
        vllm = VLLMEngine(model=model, base_url=base_url, temperature=temperature)
        register_engine("vllm", vllm, default=True)

    elif provider == "llamacpp":
        from seraphim.engine.llamacpp import LlamaCppEngine
        llamacpp = LlamaCppEngine(model=model, base_url=base_url)
        register_engine("llamacpp", llamacpp, default=True)


def register_engine(engine_id: str, engine: LLMEngine, default: bool = False) -> None:
    global _default_engine_id
    _engines[engine_id] = engine
    if default or _default_engine_id is None:
        _default_engine_id = engine_id


def get_default_engine_id() -> Optional[str]:
    _ensure_initialized()
    return _default_engine_id


def get_engine(engine_id: Optional[str] = None) -> LLMEngine:
    _ensure_initialized()

    if engine_id is None:
        if _default_engine_id is None:
            raise RuntimeError("No default engine configured")
        engine_id = _default_engine_id

    try:
        return _engines[engine_id]
    except KeyError:
        raise KeyError(f"Unknown engine_id: {engine_id!r}")
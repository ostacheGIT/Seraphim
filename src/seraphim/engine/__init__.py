from __future__ import annotations

import logging
from typing import Dict, List, Optional

from seraphim.engine.base import LLMEngine
from seraphim.engine.ollama import OllamaEngine

_engines: Dict[str, LLMEngine] = {}
_default_engine_id: Optional[str] = None
_initialized: bool = False
_gpu_available: bool = False

logger = logging.getLogger(__name__)

# Human-readable labels for the /engines endpoint
_ENGINE_LABELS: Dict[str, str] = {
    "auto":          "Auto · Routage intelligent",
    "ollama_qwen3b": "Qwen 2.5 3B · Local rapide",
    "ollama_qwen7b": "Qwen 2.5 7B · Local précis",
    "openai":        "OpenAI",
    "mistral":       "Mistral",
    "claude":        "Claude",
}


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
    try:
        from seraphim.settings import settings as _s
        _gpu_idx = _s.engine.gpu_device_index
    except Exception:
        _gpu_idx = 0
    _gpu = get_gpu_snapshot(_gpu_idx)
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
        ext = settings.external_api
    except Exception:
        provider = "ollama"
        model = "qwen2.5:3b"
        base_url = "http://localhost:11434"
        temperature = 0.7
        ext = None

    # Ollama engines (always registered)
    register_engine("ollama_qwen3b", OllamaEngine(model="qwen2.5:3b", base_url=base_url), default=(provider == "ollama"))
    register_engine("ollama_qwen7b", OllamaEngine(
        model="qwen2.5:7b-instruct-q2_k",
        base_url=base_url,
        timeout=300.0,
        options={"num_ctx": 8192},
    ), default=False)

    if provider == "vllm":
        from seraphim.engine.vllm import VLLMEngine
        register_engine("vllm", VLLMEngine(model=model, base_url=base_url, temperature=temperature), default=True)
    elif provider == "llamacpp":
        from seraphim.engine.llamacpp import LlamaCppEngine
        register_engine("llamacpp", LlamaCppEngine(model=model, base_url=base_url), default=True)

    # External API engines (registered only when keys are present)
    if ext:
        _register_external_engines(ext)


def _secret(v) -> str:
    """Unwrap a SecretStr or return the plain string."""
    try:
        return v.get_secret_value()
    except AttributeError:
        return str(v) if v else ""


def _register_external_engines(ext) -> None:
    from seraphim.engine.openai_compat import OpenAICompatEngine
    from seraphim.engine.claude import ClaudeEngine

    openai_key = _secret(ext.openai_key)
    if openai_key:
        label = "OpenAI " + ext.openai_model
        _ENGINE_LABELS["openai"] = label
        register_engine("openai", OpenAICompatEngine(
            model=ext.openai_model,
            api_key=openai_key,
            base_url=ext.openai_base_url,
            name="OpenAI",
            engine_id="openai",
        ))
        logger.info("Registered OpenAI engine (%s)", ext.openai_model)

    mistral_key = _secret(ext.mistral_key)
    if mistral_key:
        label = "Mistral " + ext.mistral_model
        _ENGINE_LABELS["mistral"] = label
        register_engine("mistral", OpenAICompatEngine(
            model=ext.mistral_model,
            api_key=mistral_key,
            base_url="https://api.mistral.ai",
            name="Mistral",
            engine_id="mistral",
        ))
        logger.info("Registered Mistral engine (%s)", ext.mistral_model)

    claude_key = _secret(ext.claude_key)
    if claude_key:
        label = "Claude " + ext.claude_model
        _ENGINE_LABELS["claude"] = label
        register_engine("claude", ClaudeEngine(
            model=ext.claude_model,
            api_key=claude_key,
        ))
        logger.info("Registered Claude engine (%s)", ext.claude_model)


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


_EXTERNAL_DEFS = [
    ("openai",  "OpenAI",  "gpt-4o-mini"),
    ("mistral", "Mistral", "mistral-small"),
    ("claude",  "Claude",  "claude-haiku"),
]

# Keys set at runtime (survive until server restart)
_runtime_keys: Dict[str, str] = {}


def list_available_engines() -> List[Dict]:
    """Return engine descriptors — always includes external engines (configured or not)."""
    _ensure_initialized()
    result: List[Dict] = [{"id": "auto", "label": _ENGINE_LABELS["auto"], "configured": True}]
    for eid in ["ollama_qwen3b", "ollama_qwen7b"]:
        if eid in _engines:
            result.append({"id": eid, "label": _ENGINE_LABELS.get(eid, eid), "configured": True})
    for eid, name, default_model in _EXTERNAL_DEFS:
        configured = eid in _engines
        label = _ENGINE_LABELS.get(eid, name) if configured else f"{name} · {default_model}"
        result.append({"id": eid, "label": label, "configured": configured})
    return result


def get_external_keys_status() -> Dict[str, bool]:
    """Return which external engines have an API key set."""
    from seraphim.settings import settings
    ext = settings.external_api
    return {
        "openai":  bool(_runtime_keys.get("openai")  or _secret(ext.openai_key)),
        "mistral": bool(_runtime_keys.get("mistral") or _secret(ext.mistral_key)),
        "claude":  bool(_runtime_keys.get("claude")  or _secret(ext.claude_key)),
    }


def update_external_key(engine_name: str, key: str) -> None:
    """Set (or clear) an external API key at runtime and persist to config.yaml."""
    import yaml
    from pathlib import Path as _Path

    # 1 — persist
    config_path = _Path.home() / ".seraphim" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if config_path.exists():
        try:
            data = yaml.safe_load(config_path.read_text()) or {}
        except Exception:
            data = {}
    data.setdefault("external_api", {})[f"{engine_name}_key"] = key
    config_path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))

    # 2 — update runtime dict
    _runtime_keys[engine_name] = key

    # 3 — re-register (or unregister) the engine
    from seraphim.settings import settings
    ext = settings.external_api
    from seraphim.engine.openai_compat import OpenAICompatEngine
    from seraphim.engine.claude import ClaudeEngine

    if engine_name == "openai":
        if key:
            _ENGINE_LABELS["openai"] = f"OpenAI · {ext.openai_model}"
            register_engine("openai", OpenAICompatEngine(
                model=ext.openai_model, api_key=key,
                base_url=ext.openai_base_url, name="OpenAI", engine_id="openai",
            ))
        else:
            _engines.pop("openai", None)

    elif engine_name == "mistral":
        if key:
            _ENGINE_LABELS["mistral"] = f"Mistral · {ext.mistral_model}"
            register_engine("mistral", OpenAICompatEngine(
                model=ext.mistral_model, api_key=key,
                base_url="https://api.mistral.ai", name="Mistral", engine_id="mistral",
            ))
        else:
            _engines.pop("mistral", None)

    elif engine_name == "claude":
        if key:
            _ENGINE_LABELS["claude"] = f"Claude · {ext.claude_model}"
            register_engine("claude", ClaudeEngine(model=ext.claude_model, api_key=key))
        else:
            _engines.pop("claude", None)

    logger.info("External engine '%s' %s", engine_name, "registered" if key else "unregistered")
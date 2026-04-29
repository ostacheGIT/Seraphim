from __future__ import annotations

from typing import Dict, Optional

from seraphim.engine.base import LLMEngine
from seraphim.engine.ollama import OllamaEngine
# from seraphim.engine.llamacpp import LlamaCppEngine  # optionnel, non utilisé pour l'instant

# Registry simple en mémoire
_engines: Dict[str, LLMEngine] = {}
_default_engine_id: Optional[str] = None
_initialized: bool = False


def _ensure_initialized() -> None:
    global _initialized
    if not _initialized:
        init_engines()
        _initialized = True


def init_engines() -> None:
    """
    Initialise les moteurs disponibles.

    Actuellement:
    - ollama_qwen3b : qwen2.5:3b (rapide, léger)
    - ollama_qwen7b : qwen2.5:7b (plus gros)
    """
    global _default_engine_id

    # Moteur 1 : qwen2.5:3b
    ollama_small = OllamaEngine(model="qwen2.5:3b")
    register_engine("ollama_qwen3b", ollama_small, default=True)

    # Moteur 2 : qwen2.5:7b
    ollama_big = OllamaEngine(model="qwen2.5:7b")
    register_engine("ollama_qwen7b", ollama_big, default=False)

    # Si tu remets llamacpp plus tard :
    # llamacpp = LlamaCppEngine()
    # register_engine("llamacpp", llamacpp, default=False)


def register_engine(engine_id: str, engine: LLMEngine, default: bool = False) -> None:
    global _default_engine_id
    _engines[engine_id] = engine
    if default or _default_engine_id is None:
        _default_engine_id = engine_id


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
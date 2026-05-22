from pathlib import Path
from pydantic import BaseModel
from pydantic_settings import BaseSettings
import yaml

class EngineSettings(BaseModel):
    provider: str = "ollama"          # ollama | vllm | llamacpp
    model: str = "llama3.2"
    base_url: str = "http://localhost:11434"
    temperature: float = 0.7
    context_window: int = 4096
    # vLLM-specific (used when provider=vllm)
    vllm_port: int = 8000
    vllm_gpu_memory_utilization: float = 0.85  # safe default for 4GB VRAM
    vllm_max_model_len: int = 4096

class ServerSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 7272
    reload: bool = False
    api_key: str = ""
    cors_origins: list[str] = ["*"]

class MemorySettings(BaseModel):
    backend: str = "sqlite"
    path: str = "~/.seraphim/memory.db"
    rag_backend: str = "sqlite_fts"   # sqlite_fts | faiss | bm25 | hybrid
    rag_enabled: bool = False
    context_top_k: int = 5
    context_min_score: float = 0.0
    context_max_tokens: int = 2048

class AgentsSettings(BaseModel):
    default: str = "chat"

class ExternalApiSettings(BaseModel):
    openai_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com"
    mistral_key: str = ""
    mistral_model: str = "mistral-small-latest"
    claude_key: str = ""
    claude_model: str = "claude-haiku-4-5-20251001"
    # Automatic fallback: if Ollama is unreachable, use this external engine
    fallback_enabled: bool = False
    fallback_engine: str = ""  # "openai" | "mistral" | "claude"

class Settings(BaseSettings):
    engine: EngineSettings = EngineSettings()
    server: ServerSettings = ServerSettings()
    memory: MemorySettings = MemorySettings()
    agents: AgentsSettings = AgentsSettings()
    external_api: ExternalApiSettings = ExternalApiSettings()
    log_level: str = "INFO"
    model_config = {"env_prefix": "SERAPHIM_", "env_nested_delimiter": "__"}

    @classmethod
    def from_yaml(cls, path: Path) -> "Settings":
        if path.exists():
            data = yaml.safe_load(path.read_text()) or {}
            return cls(**data)
        return cls()

_config_candidates = [
    Path("configs/seraphim/config.yaml"),
    Path.home() / ".seraphim" / "config.yaml",
    Path("config.yaml"),
]
_config_path = next((p for p in _config_candidates if p.exists()), Path("configs/seraphim/config.yaml"))
settings = Settings.from_yaml(_config_path)

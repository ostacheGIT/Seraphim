from pathlib import Path
from pydantic import BaseModel, SecretStr, field_validator
from pydantic_settings import BaseSettings
import yaml


class EngineSettings(BaseModel):
    provider: str = "ollama"          # ollama | vllm | llamacpp
    model: str = "llama3.2"
    base_url: str = "http://localhost:11434"
    temperature: float = 0.7
    context_window: int = 4096
    gpu_device_index: int = 0         # which GPU to monitor (multi-GPU setups)
    # vLLM-specific (used when provider=vllm)
    vllm_port: int = 8000
    vllm_gpu_memory_utilization: float = 0.85
    vllm_max_model_len: int = 4096

    @field_validator("temperature")
    @classmethod
    def _check_temperature(cls, v: float) -> float:
        if not 0.0 <= v <= 2.0:
            raise ValueError(f"temperature must be in [0.0, 2.0], got {v}")
        return v

    @field_validator("context_window")
    @classmethod
    def _check_context_window(cls, v: int) -> int:
        if v < 512:
            raise ValueError(f"context_window must be >= 512, got {v}")
        return v

    @field_validator("gpu_device_index")
    @classmethod
    def _check_gpu_device_index(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"gpu_device_index must be >= 0, got {v}")
        return v


class ServerSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 7272
    reload: bool = False
    api_key: str = ""
    cors_origins: list[str] = ["*"]

    @field_validator("port")
    @classmethod
    def _check_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError(f"port must be in [1, 65535], got {v}")
        return v


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


class LearningSettings(BaseModel):
    auto_start: bool = False
    interval_hours: float = 6.0
    min_new_traces: int = 3
    min_quality: float = 0.6
    run_grpo: bool = False
    run_finetune: bool = False


class ExternalApiSettings(BaseModel):
    # SecretStr prevents keys from appearing in logs, tracebacks, and repr()
    openai_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com"
    mistral_key: SecretStr = SecretStr("")
    mistral_model: str = "mistral-small-latest"
    claude_key: SecretStr = SecretStr("")
    claude_model: str = "claude-haiku-4-5-20251001"
    # Automatic fallback: if Ollama is unreachable, use this external engine
    fallback_enabled: bool = False
    fallback_engine: str = ""  # "openai" | "mistral" | "claude"


class TelegramSettings(BaseModel):
    enabled: bool = False
    token: SecretStr = SecretStr("")
    allowed_chat_ids: list[int] = []


class SlackSettings(BaseModel):
    enabled: bool = False
    bot_token: SecretStr = SecretStr("")
    channel_id: str = ""


class WebhookSettings(BaseModel):
    enabled: bool = False
    secret: SecretStr = SecretStr("")


class ChannelSettings(BaseModel):
    telegram: TelegramSettings = TelegramSettings()
    slack: SlackSettings = SlackSettings()
    webhook: WebhookSettings = WebhookSettings()
    auto_start: bool = False


class WorkflowSettings(BaseModel):
    directory: str = "~/.seraphim/workflows"
    max_parallel: int = 4
    timeout_secs: float = 300.0


class Settings(BaseSettings):
    engine: EngineSettings = EngineSettings()
    server: ServerSettings = ServerSettings()
    memory: MemorySettings = MemorySettings()
    agents: AgentsSettings = AgentsSettings()
    learning: LearningSettings = LearningSettings()
    external_api: ExternalApiSettings = ExternalApiSettings()
    channels: ChannelSettings = ChannelSettings()
    workflow: WorkflowSettings = WorkflowSettings()
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

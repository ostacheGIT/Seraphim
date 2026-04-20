from pathlib import Path
from pydantic import BaseModel
from pydantic_settings import BaseSettings
import yaml

class EngineSettings(BaseModel):
    provider: str = "ollama"
    model: str = "llama3.2"
    base_url: str = "http://localhost:11434"
    temperature: float = 0.7
    context_window: int = 4096

class ServerSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 7272
    reload: bool = False

class MemorySettings(BaseModel):
    backend: str = "sqlite"
    path: str = "~/.seraphim/memory.db"

class AgentsSettings(BaseModel):
    default: str = "chat"

class Settings(BaseSettings):
    engine: EngineSettings = EngineSettings()
    server: ServerSettings = ServerSettings()
    memory: MemorySettings = MemorySettings()
    agents: AgentsSettings = AgentsSettings()
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

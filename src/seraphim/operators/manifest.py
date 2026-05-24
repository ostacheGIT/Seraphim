"""OperatorManifest — declarative spec for a named, persistent agent configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class OperatorManifest:
    """Declarative description of a named agent operator.

    Stored as YAML in ~/.seraphim/operators/<name>.yaml.
    An operator binds a named agent to a specific system prompt, config,
    and optional schedule — enabling versioning and reuse across sessions.
    """

    name: str
    agent: str = "chat"
    description: str = ""
    system_prompt: str = ""          # overrides the agent's default if set
    config: dict[str, Any] = field(default_factory=dict)
    schedule: str = ""               # "HH:MM" daily, or cron expression
    enabled: bool = True
    tags: list[str] = field(default_factory=list)

    # ── Serialization ──────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":          self.name,
            "agent":         self.agent,
            "description":   self.description,
            "system_prompt": self.system_prompt,
            "config":        self.config,
            "schedule":      self.schedule,
            "enabled":       self.enabled,
            "tags":          self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OperatorManifest":
        return cls(
            name          = str(data.get("name", "")),
            agent         = str(data.get("agent", "chat")),
            description   = str(data.get("description", "")),
            system_prompt = str(data.get("system_prompt", "")),
            config        = dict(data.get("config") or {}),
            schedule      = str(data.get("schedule", "")),
            enabled       = bool(data.get("enabled", True)),
            tags          = list(data.get("tags") or []),
        )

    @classmethod
    def from_file(cls, path: Path) -> "OperatorManifest":
        try:
            import yaml  # type: ignore
            text = path.read_text(encoding="utf-8")
            data = yaml.safe_load(text) or {}
        except ImportError:
            import json
            data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)

    def save(self, directory: Path) -> Path:
        """Write manifest as YAML (or JSON fallback) to directory/<name>.yaml."""
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.name}.yaml"
        try:
            import yaml  # type: ignore
            text = yaml.dump(self.to_dict(), allow_unicode=True, sort_keys=False)
        except ImportError:
            import json
            text = json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
            path = directory / f"{self.name}.json"
        path.write_text(text, encoding="utf-8")
        return path

    def __repr__(self) -> str:
        sched = f" schedule={self.schedule!r}" if self.schedule else ""
        return f"<OperatorManifest name={self.name!r} agent={self.agent!r}{sched}>"


__all__ = ["OperatorManifest"]

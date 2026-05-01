"""Types de données pour le système de skills Seraphim."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(slots=True)
class SkillStep:
    """Une étape dans un pipeline de skill."""

    tool_name: str = ""
    skill_name: str = ""  # invoquer un autre skill plutôt qu'un outil
    arguments_template: str = "{}"  # template Jinja2
    output_key: str = ""  # clé pour stocker le résultat dans le contexte


@dataclass(slots=True)
class SkillManifest:
    """Manifeste décrivant un skill réutilisable."""

    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    steps: List[SkillStep] = field(default_factory=list)
    required_capabilities: List[str] = field(default_factory=list)
    signature: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    depends: List[str] = field(default_factory=list)
    user_invocable: bool = True
    disable_model_invocation: bool = False
    markdown_content: str = ""  # chargé depuis SKILL.md

    def manifest_bytes(self) -> bytes:
        """Sérialise le manifeste (sans signature) pour signature/vérification."""
        import json

        data = {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "steps": [
                {
                    "tool_name": s.tool_name,
                    "skill_name": s.skill_name,
                    "arguments_template": s.arguments_template,
                    "output_key": s.output_key,
                }
                for s in self.steps
            ],
            "required_capabilities": self.required_capabilities,
            "tags": self.tags,
            "depends": self.depends,
        }
        return json.dumps(data, sort_keys=True).encode()


__all__ = ["SkillManifest", "SkillStep"]

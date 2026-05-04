"""SkillTool — adapte un SkillManifest en outil natif LLM (OpenAI/Ollama tool calling).

Inspiré de OpenJarvis SkillTool. Permet aux agents d'invoquer des skills comme
des outils natifs via function calling au lieu de text parsing.
"""

from __future__ import annotations

import re
from typing import Any

from seraphim.skills.types import SkillManifest


class SkillTool:
    """Wraps un SkillManifest comme outil appelable par le LLM."""

    def __init__(self, manifest: SkillManifest, executor=None) -> None:
        self.manifest = manifest
        self._executor = executor  # SkillExecutor | None

    # ── Schema OpenAI/Ollama ─────────────────────────────────────────────────

    def to_tool_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.manifest.name,
                "description": self.manifest.description or f"Execute {self.manifest.name} skill",
                "parameters": self._build_parameters(),
            },
        }

    def _build_parameters(self) -> dict:
        if self.manifest.steps:
            # Pipeline: extract {placeholders} from argument templates, minus output keys
            produced: set[str] = set()
            needed: set[str] = set()
            for step in self.manifest.steps:
                if step.output_key:
                    produced.add(step.output_key)
                for ph in re.findall(r"\{(\w+)\}", step.arguments_template):
                    needed.add(ph)
            external = needed - produced - {"query"}
            if external:
                return {
                    "type": "object",
                    "properties": {p: {"type": "string", "description": p} for p in sorted(external)},
                    "required": sorted(external),
                }

        # Instructional skill or no external params → single task param
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task or query to execute with this skill",
                }
            },
            "required": ["task"],
        }

    # ── Exécution ─────────────────────────────────────────────────────────────

    async def execute(self, **kwargs: Any) -> str:
        if self._executor is None:
            return f"[SkillTool] No executor for {self.manifest.name}"

        query = kwargs.get("task") or kwargs.get("query") or str(kwargs)
        try:
            return await self._executor.execute(
                self.manifest,
                query,
                skill_resolver=None,
            )
        except Exception as exc:
            return f"[SkillTool error] {self.manifest.name}: {exc}"


def build_skill_tools(manifests: dict[str, SkillManifest], registry=None) -> list[SkillTool]:
    """Construit une liste de SkillTool depuis un dict name→manifest."""
    from seraphim.skills.executor import SkillExecutor

    executor = SkillExecutor(registry or {}) if registry else None
    return [
        SkillTool(m, executor)
        for m in manifests.values()
        if not m.disable_model_invocation
    ]


__all__ = ["SkillTool", "build_skill_tools"]

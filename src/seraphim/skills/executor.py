"""SkillExecutor — exécute les étapes d'un pipeline skill.toml sans LLM.

Chaque step appelle directement un outil du SKILL_REGISTRY ou délègue
à un sous-skill via un resolver callable.
"""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable

from seraphim.skills.base import BaseSkill
from seraphim.skills.types import SkillManifest, SkillStep

SkillResolverT = Callable[[str, dict], Awaitable[str]]


class SkillExecutor:
    """Exécute les steps d'un SkillManifest pipeline de façon déterministe."""

    def __init__(self, registry: dict[str, BaseSkill]) -> None:
        self._registry = registry

    async def execute(
        self,
        manifest: SkillManifest,
        query: str,
        skill_resolver: SkillResolverT | None = None,
    ) -> str:
        context: dict[str, Any] = {"query": query}
        last_output = query

        for step in manifest.steps:
            args = self._render_args(step.arguments_template, context)

            if step.skill_name:
                if skill_resolver is None:
                    last_output = f"[executor] Pas de resolver pour sous-skill '{step.skill_name}'"
                else:
                    last_output = await skill_resolver(step.skill_name, context)
            else:
                last_output = await self._run_tool(step.tool_name, args)

            if step.output_key:
                context[step.output_key] = last_output

        return last_output

    async def _run_tool(self, tool_name: str, args: dict) -> str:
        skill = self._registry.get(tool_name)
        if skill is None:
            return f"[executor] Outil '{tool_name}' absent du registry."
        try:
            result = await skill.run(**args)
            return result.output if result.success else f"Error: {result.error}"
        except Exception as exc:
            return f"Error in {tool_name}: {exc}"

    @staticmethod
    def _render_args(template: str, context: dict[str, Any]) -> dict:
        def _replace(match: re.Match) -> str:
            key = match.group(1)
            val = context.get(key, match.group(0))
            return json.dumps(val) if not isinstance(val, str) else val

        rendered = re.sub(r"\{(\w+)\}", _replace, template)
        try:
            return json.loads(rendered)
        except (json.JSONDecodeError, ValueError):
            return {"query": rendered}


__all__ = ["SkillExecutor"]

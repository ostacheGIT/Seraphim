"""DigestSkill — morning briefing: weather + news + monitor status."""

from __future__ import annotations

from seraphim.skills.base import BaseSkill, SkillResult


class DigestSkill(BaseSkill):
    name = "morning_digest"
    description = (
        "Run the morning digest: current weather, today's news headlines by topic, "
        "and monitor status. Use when user asks what's happening in the world, "
        "daily news, morning brief, 'quoi de neuf', 'nouvelles du jour', etc."
    )
    parameters = {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "City for weather (default: from user config)",
            },
            "no_summary": {
                "type": "boolean",
                "description": "Skip LLM synthesis paragraph (default: false)",
                "default": False,
            },
        },
        "required": [],
    }

    async def run(self, city: str | None = None, no_summary: bool = False, **kwargs) -> SkillResult:
        try:
            from seraphim.digest.builder import build_digest, load_config
            cfg = load_config()
            if city:
                cfg["city"] = city
            cfg["_skip_summary"] = no_summary
            digest = await build_digest(cfg)
            return SkillResult(success=True, output=digest.to_markdown())
        except Exception as e:
            return SkillResult(success=False, output="", error=str(e))

from seraphim.skills.base import BaseSkill, SkillResult


class ThinkSkill(BaseSkill):
    name = "think"
    description = (
        "Scratchpad for chain-of-thought reasoning before answering. "
        "Use to reason step-by-step, break down a problem, or plan your approach."
    )
    parameters = {
        "type": "object",
        "properties": {
            "thought": {
                "type": "string",
                "description": "Your reasoning, analysis, or planning process",
            }
        },
        "required": ["thought"],
    }

    async def run(self, thought: str, **kwargs) -> SkillResult:
        return SkillResult(success=True, output=f"[Thought]: {thought}")

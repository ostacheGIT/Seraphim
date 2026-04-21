from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

@dataclass
class SkillResult:
    success: bool
    output: str
    data: Any = None
    error: str = ""

class BaseSkill(ABC):
    name: str
    description: str
    parameters: dict  # JSON Schema pour les paramètres

    @abstractmethod
    async def run(self, **kwargs) -> SkillResult:
        ...

    def to_tool(self) -> dict:
        """Format Ollama/OpenAI tool calling"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }
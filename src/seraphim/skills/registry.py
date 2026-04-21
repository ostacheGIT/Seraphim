import importlib
import pkgutil
from seraphim.skills.base import BaseSkill

SKILL_REGISTRY: dict[str, BaseSkill] = {}

def discover_skills():
    """Découvre et enregistre tous les skills automatiquement"""
    import seraphim.skills as skills_pkg
    for _, module_name, _ in pkgutil.walk_packages(
            skills_pkg.__path__, prefix="seraphim.skills."
    ):
        module = importlib.import_module(module_name)
        for attr in dir(module):
            obj = getattr(module, attr)
            if (isinstance(obj, type) and
                    issubclass(obj, BaseSkill) and
                    obj is not BaseSkill):
                instance = obj()
                SKILL_REGISTRY[instance.name] = instance

def get_skill(name: str) -> BaseSkill:
    return SKILL_REGISTRY[name]

def get_all_tools() -> list[dict]:
    return [s.to_tool() for s in SKILL_REGISTRY.values()]
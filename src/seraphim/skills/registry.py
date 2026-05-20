import importlib
import logging
import pkgutil
from seraphim.skills.base import BaseSkill

logger = logging.getLogger(__name__)

SKILL_REGISTRY: dict[str, BaseSkill] = {}
_seen_classes: set[type] = set()  # module-level guard — prevents double-registration on re-import

def discover_skills():
    """Découvre et enregistre tous les skills automatiquement"""
    import seraphim.skills as skills_pkg
    for _, module_name, _ in pkgutil.walk_packages(
            skills_pkg.__path__, prefix="seraphim.skills."
    ):
        module = importlib.import_module(module_name)
        for attr in dir(module):
            obj = getattr(module, attr)
            if not (isinstance(obj, type) and issubclass(obj, BaseSkill) and obj is not BaseSkill):
                continue
            if obj in _seen_classes:
                continue
            _seen_classes.add(obj)
            try:
                instance = obj()
            except Exception as exc:
                logger.warning("Cannot instantiate skill %s: %s — skipping", obj.__name__, exc)
                continue
            existing = SKILL_REGISTRY.get(instance.name)
            if existing is not None:
                logger.warning(
                    "Skill name collision: '%s' defined in both %s and %s — keeping last",
                    instance.name,
                    type(existing).__module__,
                    type(instance).__module__,
                )
            SKILL_REGISTRY[instance.name] = instance

def get_skill(name: str) -> BaseSkill:
    return SKILL_REGISTRY[name]

def get_all_tools() -> list[dict]:
    return [s.to_tool() for s in SKILL_REGISTRY.values()]

"""Système de skills Seraphim — skills intégrés + import depuis sources externes."""

# --- Système de skills intégré (existant) ---
from seraphim.skills.base import BaseSkill, SkillResult
from seraphim.skills.registry import (
    SKILL_REGISTRY,
    discover_skills,
    get_all_tools,
    get_skill,
)

# --- Système d'import de skills externes ---
# Imports paresseux pour éviter les imports circulaires au démarrage du module.
# Utilisez directement : from seraphim.skills.importer import SkillImporter
# ou après init        : from seraphim.skills import SkillImporter

def __getattr__(name):
    """Import paresseux des composants du système d'import externe."""
    _lazy = {
        "ImportResult":            ("seraphim.skills.importer",        "ImportResult"),
        "SkillImporter":           ("seraphim.skills.importer",        "SkillImporter"),
        "SkillParseError":         ("seraphim.skills.parser",          "SkillParseError"),
        "SkillParser":             ("seraphim.skills.parser",          "SkillParser"),
        "TOOL_TRANSLATION":        ("seraphim.skills.tool_translator", "TOOL_TRANSLATION"),
        "ToolTranslator":          ("seraphim.skills.tool_translator", "ToolTranslator"),
        "SkillManifest":           ("seraphim.skills.types",           "SkillManifest"),
        "SkillStep":               ("seraphim.skills.types",           "SkillStep"),
        "SkillLoader":             ("seraphim.skills.loader",          "SkillLoader"),
        "detect_capabilities":     ("seraphim.skills.loader",          "detect_capabilities"),
        "SkillExecutor":           ("seraphim.skills.executor",        "SkillExecutor"),
        "SkillTool":               ("seraphim.skills.tool_adapter",    "SkillTool"),
        "build_skill_tools":       ("seraphim.skills.tool_adapter",    "build_skill_tools"),
        "SkillManager":            ("seraphim.skills.manager",         "SkillManager"),
        "get_skill_manager":       ("seraphim.skills.manager",         "get_skill_manager"),
        "TrustTier":               ("seraphim.skills.security",        "TrustTier"),
        "classify_trust_tier":     ("seraphim.skills.security",        "classify_trust_tier"),
        "validate_capabilities":   ("seraphim.skills.security",        "validate_capabilities"),
        "has_dangerous_capabilities":("seraphim.skills.security",      "has_dangerous_capabilities"),
        "GitHubResolver":          ("seraphim.skills.sources",         "GitHubResolver"),
        "HERMES_REPO_URL":         ("seraphim.skills.sources",         "HERMES_REPO_URL"),
        "HermesResolver":          ("seraphim.skills.sources",         "HermesResolver"),
        "OPENCLAW_REPO_URL":       ("seraphim.skills.sources",         "OPENCLAW_REPO_URL"),
        "OpenClawResolver":        ("seraphim.skills.sources",         "OpenClawResolver"),
        "ResolvedSkill":           ("seraphim.skills.sources",         "ResolvedSkill"),
        "SourceResolver":          ("seraphim.skills.sources",         "SourceResolver"),
        # Dependency graph
        "DependencyGraph":         ("seraphim.skills.dependency",      "DependencyGraph"),
        "build_dependency_graph":  ("seraphim.skills.dependency",      "build_dependency_graph"),
        "validate_dependencies":   ("seraphim.skills.dependency",      "validate_dependencies"),
        "compute_capability_union":("seraphim.skills.dependency",      "compute_capability_union"),
    }
    if name in _lazy:
        import importlib
        module_path, attr = _lazy[name]
        module = importlib.import_module(module_path)
        return getattr(module, attr)
    raise AttributeError(f"module 'seraphim.skills' has no attribute {name!r}")


__all__ = [
    # Système intégré
    "BaseSkill",
    "SkillResult",
    "SKILL_REGISTRY",
    "discover_skills",
    "get_all_tools",
    "get_skill",
    # Import externe (lazy)
    "detect_capabilities",
    "GitHubResolver",
    "HERMES_REPO_URL",
    "HermesResolver",
    "ImportResult",
    "OPENCLAW_REPO_URL",
    "OpenClawResolver",
    "ResolvedSkill",
    "SkillExecutor",
    "SkillImporter",
    "SkillLoader",
    "SkillManifest",
    "SkillParseError",
    "SkillParser",
    "SkillStep",
    "SourceResolver",
    "TOOL_TRANSLATION",
    "ToolTranslator",
    # Dependency graph
    "DependencyGraph",
    "build_dependency_graph",
    "validate_dependencies",
    "compute_capability_union",
]
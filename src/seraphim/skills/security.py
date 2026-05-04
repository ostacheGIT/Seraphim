"""Security — trust tiers et enforcement des capabilities.

Inspiré de OpenJarvis security.py.
BUNDLED > WORKSPACE > INDEXED > UNREVIEWED
"""

from __future__ import annotations

from enum import IntEnum
from pathlib import Path

from seraphim.skills.types import SkillManifest

# Capabilities qui nécessitent validation explicite
DANGEROUS_CAPS: frozenset[str] = frozenset({
    "shell:execute",
    "network:listen",
    "filesystem:write",
})

# Capabilities autorisées par tier
# INDEXED = sources connues (hermes, openclaw, skillssh) → shell autorisé
# UNREVIEWED = source inconnue → pas de shell ni d'écriture
_TIER_ALLOWED: dict[int, set[str]] = {
    4: {"shell:execute", "network:fetch", "network:listen",
        "filesystem:read", "filesystem:write", "memory:read", "memory:write", "engine:inference"},
    3: {"shell:execute", "network:fetch", "filesystem:read",
        "filesystem:write", "memory:read", "memory:write", "engine:inference"},
    2: {"shell:execute", "network:fetch", "filesystem:read",
        "filesystem:write", "memory:read", "memory:write", "engine:inference"},
    1: {"network:fetch", "filesystem:read", "engine:inference"},
}


class TrustTier(IntEnum):
    UNREVIEWED = 1   # Source GitHub arbitraire
    INDEXED    = 2   # Source indexée (hermes, openclaw, skillssh)
    WORKSPACE  = 3   # Skills locaux ~/.seraphim/skills/workspace/
    BUNDLED    = 4   # Livré avec Seraphim (core/, system/, web/)


def classify_trust_tier(skill_dir: Path, source: str) -> TrustTier:
    """Détermine le tier de confiance d'un skill depuis son répertoire et sa source."""
    if source in ("core", "bundled", "system", "web"):
        return TrustTier.BUNDLED

    if source == "workspace" or "workspace" in str(skill_dir):
        return TrustTier.WORKSPACE

    # Skills installés depuis sources connues (hermes, openclaw, skillssh, leoye, autonomys)
    known_indexed = {"hermes", "openclaw", "skillssh", "leoye", "autonomys", "voltagent"}
    if source in known_indexed:
        dot_source = skill_dir / ".source"
        if dot_source.exists():
            return TrustTier.INDEXED

    return TrustTier.UNREVIEWED


def has_dangerous_capabilities(manifest: SkillManifest) -> bool:
    """True si le skill demande des capabilities dangereuses."""
    return bool(set(manifest.required_capabilities) & DANGEROUS_CAPS)


def validate_capabilities(
    manifest: SkillManifest,
    tier: TrustTier,
    *,
    extra_allowed: set[str] | None = None,
) -> list[str]:
    """Retourne la liste des capabilities requises non autorisées pour ce tier.

    Liste vide = skill autorisé à s'exécuter.
    """
    allowed = _TIER_ALLOWED.get(int(tier), set()).copy()
    if extra_allowed:
        allowed |= extra_allowed
    return [c for c in manifest.required_capabilities if c not in allowed]


def get_tier_warning(tier: TrustTier) -> str | None:
    """Retourne un message d'avertissement si le tier est bas."""
    if tier == TrustTier.UNREVIEWED:
        return "⚠ Skill non vérifié (source inconnue) — exécuté avec capabilities limitées."
    if tier == TrustTier.INDEXED:
        return None
    return None


__all__ = [
    "TrustTier",
    "DANGEROUS_CAPS",
    "classify_trust_tier",
    "has_dangerous_capabilities",
    "validate_capabilities",
    "get_tier_warning",
]

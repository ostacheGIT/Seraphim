"""SkillManager — découverte, catalog XML, overlays d'optimisation.

Inspiré de OpenJarvis SkillManager.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator, List

from seraphim.skills.loader import SkillLoader
from seraphim.skills.types import SkillManifest

logger = logging.getLogger(__name__)

_SKILLS_ROOT = Path("~/.seraphim/skills").expanduser()
_OVERLAY_ROOT = Path("~/.seraphim/learning/skills").expanduser()


class SkillManager:
    """Gère le catalogue de skills installés.

    - Découverte depuis ~/.seraphim/skills/
    - Génération du catalog XML injecté dans les system prompts
    - Application des overlays d'optimisation (DSPy/GEPA)
    - Few-shot examples depuis les traces
    - Validation du graphe de dépendances
    """

    def __init__(self, skills_root: Path | None = None) -> None:
        self._root = Path(skills_root or _SKILLS_ROOT)
        self._loader = SkillLoader()
        self._manifests: dict[str, SkillManifest] = {}
        self._paths: dict[str, Path] = {}  # name → skill_dir

    # ── Découverte ────────────────────────────────────────────────────────────

    def discover(self) -> dict[str, SkillManifest]:
        """Scanne ~/.seraphim/skills/ et charge tous les manifests.

        Priorité: premier trouvé gagne (workspace > indexed > unreviewed).
        """
        self._manifests.clear()
        self._paths.clear()
        for skill_dir in self._iter_skill_dirs():
            name = skill_dir.name
            if name in self._manifests:
                continue
            try:
                manifest = self._loader.load(skill_dir)
                manifest = self._apply_overlay(manifest, skill_dir)
                self._manifests[name] = manifest
                self._paths[name] = skill_dir
            except Exception as exc:
                logger.debug("Skill load failed %s: %s", skill_dir, exc)

        errors = self.validate_dependency_graph()
        for err in errors:
            logger.warning("Dependency issue: %s", err)

        return self._manifests

    def _iter_skill_dirs(self) -> Iterator[Path]:
        if not self._root.exists():
            return
        # workspace first for priority
        for source_dir in sorted(self._root.iterdir()):
            if not source_dir.is_dir():
                continue
            order = 0 if source_dir.name == "workspace" else 1
            for skill_dir in sorted(source_dir.iterdir()):
                if skill_dir.is_dir() and (
                    (skill_dir / "SKILL.md").exists()
                    or (skill_dir / "skill.toml").exists()
                ):
                    yield skill_dir

    # ── Overlays d'optimisation ───────────────────────────────────────────────

    def _apply_overlay(self, manifest: SkillManifest, skill_dir: Path) -> SkillManifest:
        """Applique l'overlay DSPy/GEPA si présent — ne modifie jamais l'original."""
        overlay_path = _OVERLAY_ROOT / manifest.name / "optimized.toml"
        if not overlay_path.exists():
            return manifest

        try:
            import sys
            if sys.version_info >= (3, 11):
                import tomllib
            else:
                import tomli as tomllib  # type: ignore

            data = tomllib.loads(overlay_path.read_bytes().decode())
            if "description" in data:
                manifest.description = data["description"]
            few_shot = data.get("few_shot", [])
            if few_shot:
                meta = dict(manifest.metadata)
                seraphim_meta = dict(meta.get("seraphim") or {})
                seraphim_meta["few_shot"] = few_shot
                meta["seraphim"] = seraphim_meta
                manifest.metadata = meta
        except Exception as exc:
            logger.debug("Overlay apply failed %s: %s", manifest.name, exc)

        return manifest

    # ── Catalog XML ───────────────────────────────────────────────────────────

    def get_catalog_xml(self) -> str:
        """Génère le catalog XML à injecter dans les system prompts agents.

        Les agents voient les skills disponibles et peuvent les invoquer.
        """
        if not self._manifests:
            self.discover()

        lines = ["<available_skills>"]
        for name, manifest in sorted(self._manifests.items()):
            if manifest.disable_model_invocation:
                continue
            desc = (manifest.description or "").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
            caps = ",".join(manifest.required_capabilities) if manifest.required_capabilities else ""
            lines.append(
                f'  <skill name="{name}" description="{desc}"'
                + (f' capabilities="{caps}"' if caps else "")
                + "/>"
            )
        lines.append("</available_skills>")
        return "\n".join(lines)

    # ── Few-shot examples ─────────────────────────────────────────────────────

    def get_few_shot_examples(self) -> str:
        """Retourne les exemples few-shot depuis les overlays pour injection dans prompts."""
        parts: list[str] = []
        for name, manifest in self._manifests.items():
            few_shot = (manifest.metadata.get("seraphim") or {}).get("few_shot", [])
            for ex in few_shot:
                inp = ex.get("input", "")
                out = ex.get("output", "")
                if inp and out:
                    parts.append(f"### {name}\nInput: {inp}\nOutput: {out}")
        return "\n\n".join(parts)

    # ── Dépendances ────────────────────────────────────────────────────────────

    def validate_dependency_graph(self) -> List[str]:
        """Valide les dépendances des skills installés — retourne la liste d'erreurs."""
        if not self._manifests:
            return []
        try:
            from seraphim.skills.dependency import build_dependency_graph, validate_dependencies
            graph = build_dependency_graph(self._manifests)
            return validate_dependencies(graph)
        except Exception as exc:
            logger.debug("Dependency validation error: %s", exc)
            return []

    def compute_capability_union(self, name: str) -> List[str]:
        """Retourne l'union transitive des capabilities requises pour un skill."""
        try:
            from seraphim.skills.dependency import build_dependency_graph, compute_capability_union
            graph = build_dependency_graph(self._manifests)
            return compute_capability_union(name, graph)
        except Exception:
            manifest = self._manifests.get(name)
            return list(manifest.required_capabilities) if manifest else []

    # ── Outils natifs ──────────────────────────────────────────────────────────

    def get_skill_tools(self) -> list:
        """Retourne les skills installés comme SkillTool (function calling natif)."""
        from seraphim.skills.tool_adapter import build_skill_tools
        from seraphim.skills.registry import SKILL_REGISTRY
        if not self._manifests:
            self.discover()
        return build_skill_tools(self._manifests, registry=SKILL_REGISTRY)

    def find_installed_paths(self) -> List[Path]:
        """Retourne les répertoires des skills installés."""
        if not self._paths:
            self.discover()
        return list(self._paths.values())

    # ── Accès ─────────────────────────────────────────────────────────────────

    def get(self, name: str) -> SkillManifest | None:
        return self._manifests.get(name)

    def get_path(self, name: str) -> Path | None:
        return self._paths.get(name)

    def __len__(self) -> int:
        return len(self._manifests)

    def __iter__(self):
        return iter(self._manifests.items())


# Singleton global
_manager: SkillManager | None = None


def get_skill_manager() -> SkillManager:
    global _manager
    if _manager is None:
        _manager = SkillManager()
    return _manager


__all__ = ["SkillManager", "get_skill_manager"]

"""OpenClawResolver — résout les skills depuis openclaw/openclaw.

Structure réelle du repo openclaw/openclaw :
    skills/<skill-name>/SKILL.md   (structure plate, pas d'owner)
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import List

import yaml

from seraphim.skills.sources.base import ResolvedSkill, SourceResolver

LOGGER = logging.getLogger(__name__)

# Repo principal openclaw — les skills sont dans le sous-dossier skills/
OPENCLAW_REPO_URL = "https://github.com/openclaw/openclaw.git"


class OpenClawResolver(SourceResolver):
    """Résout les skills depuis le repo openclaw/openclaw (skills/ plat)."""

    name = "openclaw"

    def __init__(self, cache_root: Path | None = None) -> None:
        if cache_root is None:
            cache_root = Path("~/.seraphim/skill-cache/openclaw/").expanduser()
        self._cache_root = Path(cache_root)

    def cache_dir(self) -> Path:
        return self._cache_root

    def sync(self) -> None:
        if self._cache_root.exists() and (self._cache_root / ".git").exists():
            subprocess.run(
                ["git", "-C", str(self._cache_root), "pull", "--ff-only"],
                check=True,
            )
        else:
            self._cache_root.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", "--depth=1", OPENCLAW_REPO_URL, str(self._cache_root)],
                check=True,
            )

    def list_skills(self) -> List[ResolvedSkill]:
        """skills/<skill-name>/SKILL.md — structure plate."""
        skills_root = self._cache_root / "skills"
        if not skills_root.exists():
            return []

        results: List[ResolvedSkill] = []
        commit = self._read_commit()

        for skill_dir in sorted(skills_root.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            name, description = self._read_preview(skill_md, default_name=skill_dir.name)
            sidecar = self._read_sidecar(skill_dir / "_meta.json")
            results.append(
                ResolvedSkill(
                    name=name,
                    source=self.name,
                    path=skill_dir,
                    category="openclaw",
                    description=description,
                    commit=commit,
                    sidecar_data=sidecar,
                )
            )

        return results

    def _read_preview(self, skill_md: Path, default_name: str) -> tuple[str, str]:
        try:
            raw = skill_md.read_text(encoding="utf-8")
        except Exception:
            return default_name, ""
        if not raw.startswith("---"):
            return default_name, ""
        rest = raw[3:].lstrip("\n")
        end = rest.find("\n---")
        if end == -1:
            return default_name, ""
        try:
            fm = yaml.safe_load(rest[:end])
        except yaml.YAMLError:
            return default_name, ""
        if not isinstance(fm, dict):
            return default_name, ""
        return str(fm.get("name", default_name)), str(fm.get("description", ""))

    def _read_sidecar(self, sidecar_path: Path) -> dict:
        if not sidecar_path.exists():
            return {}
        try:
            return json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _read_commit(self) -> str:
        if not (self._cache_root / ".git").exists():
            return ""
        try:
            result = subprocess.run(
                ["git", "-C", str(self._cache_root), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return ""


__all__ = ["OpenClawResolver", "OPENCLAW_REPO_URL"]

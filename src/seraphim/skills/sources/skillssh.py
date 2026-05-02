"""SkillsShResolver — intègre le registre skills.sh (90 000+ skills).

Deux modes:
  sync()     → clone vercel-labs/agent-skills (7 skills officiels Vercel)
  resolve()  → cherche via skills.sh/api/search + fetch SKILL.md depuis GitHub

Utilisation:
    seraphim skill sync --source skillssh          # clone vercel officiel
    seraphim skill search browser --source skillssh  # recherche live
    seraphim skill import agent-browser --source skillssh  # install depuis skills.sh
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import List

import httpx
import yaml

from seraphim.skills.sources.base import ResolvedSkill, SourceResolver

LOGGER = logging.getLogger(__name__)

VERCEL_AGENT_SKILLS_URL = "https://github.com/vercel-labs/agent-skills.git"
SKILLS_SH_SEARCH_API   = "https://skills.sh/api/search"
RAW_GH                 = "https://raw.githubusercontent.com/{owner_repo}/main/SKILL.md"
RAW_GH_FALLBACKS       = [
    "https://raw.githubusercontent.com/{owner_repo}/master/SKILL.md",
    "https://raw.githubusercontent.com/{owner_repo}/main/skills/{slug}/SKILL.md",
]


class SkillsShResolver(SourceResolver):
    """Résout les skills depuis le registre skills.sh."""

    name = "skillssh"

    def __init__(self, cache_root: Path | None = None) -> None:
        if cache_root is None:
            cache_root = Path("~/.seraphim/skill-cache/skillssh/").expanduser()
        self._cache_root = Path(cache_root)

    def cache_dir(self) -> Path:
        return self._cache_root

    # ── Sync : clone vercel-labs/agent-skills ─────────────────────────────────

    def sync(self) -> None:
        """Clone/pull vercel-labs/agent-skills (skills officiels Vercel)."""
        if self._cache_root.exists() and (self._cache_root / ".git").exists():
            subprocess.run(
                ["git", "-C", str(self._cache_root), "pull", "--ff-only"],
                check=True,
            )
        else:
            self._cache_root.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", "--depth=1", VERCEL_AGENT_SKILLS_URL, str(self._cache_root)],
                check=True,
            )

    # ── list_skills : depuis le cache vercel cloné ────────────────────────────

    def list_skills(self) -> List[ResolvedSkill]:
        """Liste les skills du cache vercel-labs/agent-skills."""
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
            name, desc = self._read_preview(skill_md, skill_dir.name)
            results.append(ResolvedSkill(
                name=name,
                source=self.name,
                path=skill_dir,
                category="vercel",
                description=desc,
                commit=commit,
            ))
        return results

    # ── resolve : recherche live skills.sh + fetch GitHub ────────────────────

    def resolve(self, query: str) -> List[ResolvedSkill]:
        """
        Cherche sur skills.sh/api/search, télécharge SKILL.md depuis GitHub,
        sauvegarde dans le cache local, retourne les résultats.
        """
        if not query:
            return self.list_skills()

        try:
            resp = httpx.get(
                SKILLS_SH_SEARCH_API,
                params={"q": query},
                timeout=10.0,
                follow_redirects=True,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            LOGGER.warning("skills.sh search failed: %s", exc)
            # Fallback: recherche locale dans le cache cloné
            return super().resolve(query)

        skills_data = data.get("skills", [])
        if not skills_data:
            return []

        results: List[ResolvedSkill] = []
        for item in skills_data[:20]:
            owner_repo = item.get("source", "")
            slug = item.get("skillId") or item.get("name") or owner_repo.split("/")[-1]
            if not owner_repo:
                continue

            skill_dir, content = self._fetch_and_cache(owner_repo, slug)
            if not skill_dir:
                continue

            name = item.get("name") or slug
            desc = self._extract_description(content)

            results.append(ResolvedSkill(
                name=name,
                source=self.name,
                path=skill_dir,
                category=owner_repo.split("/")[0],
                description=desc,
                commit="",
            ))

        return results

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _fetch_and_cache(self, owner_repo: str, slug: str) -> tuple[Path | None, str]:
        """Télécharge SKILL.md depuis GitHub et le met en cache local."""
        cache_dir = self._cache_root / "remote" / slug
        skill_md  = cache_dir / "SKILL.md"

        # Déjà en cache → lecture directe
        if skill_md.exists():
            return skill_md.parent, skill_md.read_text(encoding="utf-8", errors="replace")

        # Tentatives de téléchargement
        urls = [RAW_GH.format(owner_repo=owner_repo)] + [
            f.format(owner_repo=owner_repo, slug=slug) for f in RAW_GH_FALLBACKS
        ]
        content = ""
        for url in urls:
            try:
                r = httpx.get(url, timeout=8.0, follow_redirects=True)
                if r.status_code == 200:
                    content = r.text
                    break
            except Exception:
                continue

        if not content:
            LOGGER.debug("No SKILL.md found for %s", owner_repo)
            return None, ""

        cache_dir.mkdir(parents=True, exist_ok=True)
        skill_md.write_text(content, encoding="utf-8")
        return cache_dir, content

    def _extract_description(self, content: str) -> str:
        if not content.startswith("---"):
            return ""
        rest = content[3:].lstrip("\n")
        end  = rest.find("\n---")
        if end == -1:
            return ""
        try:
            fm = yaml.safe_load(rest[:end])
            if isinstance(fm, dict):
                return str(fm.get("description", ""))
        except yaml.YAMLError:
            pass
        return ""

    def _read_preview(self, skill_md: Path, default_name: str) -> tuple[str, str]:
        try:
            raw = skill_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return default_name, ""
        return default_name, self._extract_description(raw)

    def _read_commit(self) -> str:
        if not (self._cache_root / ".git").exists():
            return ""
        try:
            r = subprocess.run(
                ["git", "-C", str(self._cache_root), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True,
            )
            return r.stdout.strip()
        except subprocess.CalledProcessError:
            return ""


__all__ = ["SkillsShResolver"]

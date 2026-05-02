"""Skill catalog — index plat de tous les skills disponibles (openclaw + hermes + installés).

Workflow:
    seraphim skill build-index   →  scanne les caches, écrit ~/.seraphim/skill-catalog.json
    search_skills(query, top_k)  →  retourne les N skills les plus pertinents (in-memory, rapide)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List

import yaml

LOGGER = logging.getLogger(__name__)

CATALOG_PATH = Path("~/.seraphim/skill-catalog.json")

# In-memory cache — chargé une seule fois par processus
_catalog_cache: list[Dict] | None = None


# ── Parsing frontmatter ────────────────────────────────────────────────────────

def _read_frontmatter(path: Path, default_name: str) -> tuple[str, str]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
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


# ── Scanners par source ─────────────────────────���──────────────────────────────

def _scan_openclaw(cache_root: Path, entries: list) -> int:
    """skills/<skill-name>/SKILL.md — structure plate (openclaw/openclaw repo)."""
    skills_root = cache_root / "skills"
    if not skills_root.exists():
        return 0
    count = 0
    for skill_dir in skills_root.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        name, desc = _read_frontmatter(skill_md, skill_dir.name)
        entries.append({
            "name": name,
            "slug": skill_dir.name,
            "description": desc,
            "source": "openclaw",
            "category": "openclaw",
        })
        count += 1
    return count


def _scan_hermes(cache_root: Path, entries: list) -> int:
    """skills/<category>/<skill>/SKILL.md + optional-skills/<category>/<skill>/SKILL.md"""
    count = 0
    for subdir_name in ("skills", "optional-skills"):
        skills_root = cache_root / subdir_name
        if not skills_root.exists():
            continue
        for category_dir in skills_root.iterdir():
            if not category_dir.is_dir():
                continue
            for skill_dir in category_dir.iterdir():
                if not skill_dir.is_dir():
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue
                name, desc = _read_frontmatter(skill_md, skill_dir.name)
                entries.append({
                    "name": name,
                    "slug": skill_dir.name,
                    "description": desc,
                    "source": "hermes",
                    "category": category_dir.name,
                })
                count += 1
    return count


def _scan_skillssh(cache_root: Path, entries: list) -> int:
    """skills.sh: skills/<name>/SKILL.md (vercel clone) + remote/<name>/SKILL.md (fetched)."""
    count = 0
    for subdir in ("skills", "remote"):
        root = cache_root / subdir
        if not root.exists():
            continue
        for skill_dir in root.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            name, desc = _read_frontmatter(skill_md, skill_dir.name)
            entries.append({
                "name": name,
                "slug": skill_dir.name,
                "description": desc,
                "source": "skillssh",
                "category": "vercel" if subdir == "skills" else "remote",
            })
            count += 1
    return count


def _scan_installed(skills_root: Path, entries: list) -> int:
    """~/.seraphim/skills/<source?>/<skill>/SKILL.md  (rglob)"""
    count = 0
    for skill_md in skills_root.rglob("SKILL.md"):
        skill_dir = skill_md.parent
        name, desc = _read_frontmatter(skill_md, skill_dir.name)
        entries.append({
            "name": name,
            "slug": skill_dir.name,
            "description": desc,
            "source": "installed",
            "category": skill_dir.parent.name,
        })
        count += 1
    return count


# ── Builder public ────────────────────────────────────────────────────────────��

def build_catalog(progress_callback=None) -> int:
    """
    Scanne les caches openclaw + hermes + skills installés.
    Écrit l'index dans ~/.seraphim/skill-catalog.json.
    Retourne le nombre de skills indexés.

    progress_callback(source: str, count: int) appelé après chaque source.
    """
    global _catalog_cache
    entries: list[Dict] = []

    openclaw_root = Path("~/.seraphim/skill-cache/openclaw").expanduser()
    n = _scan_openclaw(openclaw_root, entries)
    if progress_callback:
        progress_callback("openclaw", n)

    hermes_root = Path("~/.seraphim/skill-cache/hermes").expanduser()
    n = _scan_hermes(hermes_root, entries)
    if progress_callback:
        progress_callback("hermes", n)

    # skills.sh — vercel officiel + skills fetchés à la demande
    skillssh_root = Path("~/.seraphim/skill-cache/skillssh").expanduser()
    n = _scan_skillssh(skillssh_root, entries)
    if progress_callback:
        progress_callback("skillssh", n)

    installed_root = Path("~/.seraphim/skills").expanduser()
    n = _scan_installed(installed_root, entries)
    if progress_callback:
        progress_callback("installed", n)

    catalog_path = CATALOG_PATH.expanduser()
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(
        json.dumps(entries, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    _catalog_cache = entries
    LOGGER.info("Skill catalog built: %d entries → %s", len(entries), catalog_path)
    return len(entries)


# ── Chargement in-memory ─────────────────────────────────��─────────────────────

def _load_catalog() -> list[Dict]:
    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache
    p = CATALOG_PATH.expanduser()
    if p.exists():
        try:
            _catalog_cache = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _catalog_cache = []
    else:
        _catalog_cache = []
    return _catalog_cache


def get_catalog_size() -> int:
    return len(_load_catalog())


# ── Recherche ─────────────────────────────��───────────────────────────────��───

def search_skills(query: str, top_k: int = 15) -> list[Dict]:
    """
    Recherche les skills pertinents pour une requête.
    Scoring: nombre de mots de la query trouvés dans name+description.
    Retourne les top_k meilleurs.
    """
    catalog = _load_catalog()
    if not catalog:
        return []

    q_words = [w for w in query.lower().split() if len(w) > 2]
    if not q_words:
        return []

    scored: list[tuple[int, Dict]] = []
    for entry in catalog:
        text = f"{entry['name']} {entry.get('description', '')} {entry.get('category', '')}".lower()
        score = sum(1 for w in q_words if w in text)
        if score > 0:
            # Bonus: mot exact dans le nom du skill
            if any(w in entry["name"].lower() for w in q_words):
                score += 2
            scored.append((score, entry))

    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[:top_k]]


def format_skill_catalog_block(skills: list[Dict]) -> str:
    """Formatte les skills pour injection dans un prompt LLM."""
    if not skills:
        return ""
    lines = [
        "\n\n## External Skills disponibles",
        "Pour utiliser un skill externe, écris exactement:",
        "ACTION: skill:<slug-du-skill>",
        'ARGS: {"query": "ta demande précise"}',
        "",
    ]
    for s in skills:
        slug = s.get("slug") or s["name"]
        desc = s.get("description", "")[:120]
        src = s.get("source", "?")
        lines.append(f"- skill:{slug} [{src}/{s.get('category','')}] — {desc}")
    return "\n".join(lines)


__all__ = [
    "build_catalog",
    "search_skills",
    "format_skill_catalog_block",
    "get_catalog_size",
    "CATALOG_PATH",
]

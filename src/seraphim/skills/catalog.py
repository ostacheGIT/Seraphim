"""Skill catalog — index plat de tous les skills disponibles (caches + installés).

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


# ── Scanners par source ───────────────────────────────────────────────────────

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
    if not skills_root.exists():
        return 0
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


def _scan_generic(cache_root: Path, entries: list) -> int:
    """Scanner générique pour sources non connues (voltagent, leoye, autonomys, etc.)."""
    count = 0
    source_name = cache_root.name
    if not cache_root.exists():
        return 0
    for skill_md in cache_root.rglob("SKILL.md"):
        skill_dir = skill_md.parent
        try:
            rel_parts = skill_md.relative_to(cache_root).parts
        except ValueError:
            continue
        # Skip root-level SKILL.md (the repo itself, not a skill)
        if len(rel_parts) <= 1:
            continue
        if len(rel_parts) > 6:
            continue
        # Skip slug matching source name (repo root false entry)
        if skill_dir.name == source_name:
            continue
        name, desc = _read_frontmatter(skill_md, skill_dir.name)
        entries.append({
            "name": name,
            "slug": skill_dir.name,
            "description": desc,
            "source": source_name,
            "category": skill_dir.parent.name,
        })
        count += 1
    return count


# ── Builder public ────────────────────────────────────────────────────────────

def build_catalog(progress_callback=None) -> int:
    """
    Scanne dynamiquement tous les caches dans ~/.seraphim/skill-cache/
    + les skills installés dans ~/.seraphim/skills/.
    Écrit l'index dans ~/.seraphim/skill-catalog.json.
    Retourne le nombre de skills indexés.

    progress_callback(source: str, count: int) appelé après chaque source.
    """
    global _catalog_cache
    entries: list[Dict] = []

    cache_base = Path("~/.seraphim/skill-cache").expanduser()

    # Scanners dédiés pour les sources connues, fallback générique pour les autres
    DEDICATED_SCANNERS = {
        "openclaw": _scan_openclaw,
        "hermes":   _scan_hermes,
        "skillssh": _scan_skillssh,
    }

    if cache_base.exists():
        for source_dir in sorted(cache_base.iterdir()):
            if not source_dir.is_dir():
                continue
            source_name = source_dir.name
            scanner = DEDICATED_SCANNERS.get(source_name, _scan_generic)
            n = scanner(source_dir, entries)
            LOGGER.debug("Scanned %s: %d skills", source_name, n)
            if progress_callback:
                progress_callback(source_name, n)

    # Skills importés manuellement
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

    _catalog_cache = _dedup_catalog(entries)
    LOGGER.info("Skill catalog built: %d raw → %d deduped → %s",
                len(entries), len(_catalog_cache), catalog_path)
    return len(_catalog_cache)


# ── Chargement in-memory ──────────────────────────────────────────────────────

_SOURCE_PRIORITY = {
    "installed": 0,
    "skillssh": 1,
    "hermes": 2,
    "openclaw": 3,
    "voltagent": 4,
    "autonomys": 5,
    "leoye": 6,
}


def _dedup_catalog(entries: list[Dict]) -> list[Dict]:
    """Keep one entry per skill name (highest-priority source wins), sorted by name."""
    best: dict[str, tuple[int, Dict]] = {}
    for entry in entries:
        name = entry["name"].lower()
        prio = _SOURCE_PRIORITY.get(entry.get("source", ""), 99)
        if name not in best or prio < best[name][0]:
            best[name] = (prio, entry)
    deduped = [e for _, e in best.values()]
    deduped.sort(key=lambda e: e["name"].lower())
    return deduped


def _load_catalog() -> list[Dict]:
    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache
    p = CATALOG_PATH.expanduser()
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            _catalog_cache = _dedup_catalog(raw)
        except (json.JSONDecodeError, OSError):
            _catalog_cache = []
    else:
        _catalog_cache = []
    return _catalog_cache


def get_catalog_size() -> int:
    return len(_load_catalog())


# ── Recherche ─────────────────────────────────────────────────────────────────

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
        name_lower = entry["name"].lower()
        desc_lower = entry.get("description", "").lower()

        desc_hits = sum(1 for w in q_words if w in desc_lower)
        name_hits = sum(1 for w in q_words if w in name_lower)
        score = desc_hits + name_hits
        if score == 0:
            continue
        # Bonus: all query words found in description (more specific than name match)
        if desc_hits == len(q_words):
            score += 3
        # Bonus: any word in name
        if name_hits:
            score += 1
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


def list_catalog(limit: int = 200, offset: int = 0, source: str = "") -> list[Dict]:
    """Return catalog entries sorted by name, optionally filtered by source."""
    catalog = _load_catalog()
    if source:
        catalog = [e for e in catalog if e.get("source") == source]
    return catalog[offset: offset + limit]


__all__ = [
    "build_catalog",
    "list_catalog",
    "search_skills",
    "format_skill_catalog_block",
    "get_catalog_size",
    "CATALOG_PATH",
]
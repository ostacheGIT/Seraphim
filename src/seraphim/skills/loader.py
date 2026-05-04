"""SkillLoader — charge SKILL.md et/ou skill.toml → SkillManifest unifié.

Ordre de priorité :
  1. skill.toml  → pipeline déterministe + métadonnées
  2. SKILL.md    → instructions LLM + YAML frontmatter
  3. Les deux    → pipeline + markdown fusionnés

Détection automatique des capabilities si non déclarées.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

from seraphim.skills.types import SkillManifest, SkillStep

# ── tomllib compat (3.10 supporte pas tomllib natif) ────────────────────────
if sys.version_info >= (3, 11):
    import tomllib as _tomllib
else:
    try:
        import tomllib as _tomllib
    except ImportError:
        try:
            import tomli as _tomllib  # type: ignore[no-redef]
        except ImportError:
            _tomllib = None  # type: ignore[assignment]

# ── Capability auto-detection patterns ──────────────────────────────────────
_CAP_PATTERNS: dict[str, list[str]] = {
    "shell:execute": [
        r"\bBash\b", r"\bgh\b", r"\bgit\b", r"\bcurl\b", r"\bnpm\b",
        r"\bpip\b", r"allowed-tools", r"\bshell_exec\b",
        r"```(?:bash|sh|shell)", r"\$\(", r"\bpwsh\b", r"\bcmd\b",
    ],
    "network:fetch": [
        r"\bWebFetch\b", r"\bWebSearch\b", r"\bweb_search\b",
        r"https?://", r"\bfetch\b.*\burl\b",
    ],
    "filesystem:read": [
        r"\bRead\b", r"\bfile_read\b", r"\bread_file\b",
    ],
    "filesystem:write": [
        r"\bWrite\b", r"\bfile_write\b", r"\bwrite_file\b", r"\bEdit\b",
    ],
}

# allowed-tools → capabilities (Claude Code naming)
_ALLOWED_TOOLS_MAP: dict[str, str] = {
    "Bash": "shell:execute",
    "bash": "shell:execute",
    "shell_exec": "shell:execute",
    "WebFetch": "network:fetch",
    "WebSearch": "network:fetch",
    "web_search": "network:fetch",
    "Read": "filesystem:read",
    "file_read": "filesystem:read",
    "Write": "filesystem:write",
    "file_write": "filesystem:write",
    "Edit": "filesystem:write",
}


def detect_capabilities(content: str) -> list[str]:
    """Détecte les capabilities requises depuis le contenu markdown d'un skill."""
    caps: list[str] = []
    for cap, patterns in _CAP_PATTERNS.items():
        if any(re.search(p, content) for p in patterns):
            caps.append(cap)
    return caps


class SkillLoader:
    """Charge un répertoire de skill et retourne un SkillManifest unifié."""

    def load(self, skill_dir: Path) -> SkillManifest:
        toml_path = skill_dir / "skill.toml"
        md_path = skill_dir / "SKILL.md"
        if not md_path.exists():
            md_path = skill_dir / "skill.md"

        has_toml = toml_path.exists()
        has_md = md_path.exists()

        if has_toml and has_md:
            manifest = self._load_toml(toml_path)
            manifest.markdown_content = md_path.read_text(encoding="utf-8")
            return manifest
        elif has_toml:
            return self._load_toml(toml_path)
        elif has_md:
            return self._load_md(md_path)
        else:
            raise FileNotFoundError(f"Aucun skill.toml ou SKILL.md dans {skill_dir}")

    # ── TOML pipeline ────────────────────────────────────────────────────────

    def _load_toml(self, path: Path) -> SkillManifest:
        if _tomllib is None:
            raise ImportError(
                "tomllib non disponible. Python <3.11 : pip install tomli"
            )
        data = _tomllib.loads(path.read_bytes().decode())
        skill_data: dict = data.get("skill", data)

        steps: list[SkillStep] = []
        for s in skill_data.get("steps", []):
            steps.append(SkillStep(
                tool_name=s.get("tool_name", ""),
                skill_name=s.get("skill_name", ""),
                arguments_template=s.get("arguments_template", "{}"),
                output_key=s.get("output_key", "result"),
            ))

        return SkillManifest(
            name=str(skill_data.get("name", path.parent.name)),
            description=str(skill_data.get("description", "")),
            version=str(skill_data.get("version", "0.1.0")),
            author=str(skill_data.get("author", "")),
            steps=steps,
            required_capabilities=list(skill_data.get("required_capabilities", [])),
            tags=list(skill_data.get("tags") or []),
            depends=list(skill_data.get("depends") or []),
            user_invocable=bool(skill_data.get("user_invocable", True)),
            disable_model_invocation=bool(
                skill_data.get("disable_model_invocation", False)
            ),
        )

    # ── Markdown instructions ────────────────────────────────────────────────

    def _load_md(self, path: Path) -> SkillManifest:
        raw = path.read_text(encoding="utf-8")
        fm, body = self._parse_frontmatter(raw)

        caps: list[str] = list(fm.get("required_capabilities") or [])

        if not caps:
            allowed = str(fm.get("allowed-tools", ""))
            for token in allowed.split():
                base = token.split("(")[0]
                mapped = _ALLOWED_TOOLS_MAP.get(base)
                if mapped and mapped not in caps:
                    caps.append(mapped)

        if not caps:
            caps = detect_capabilities(body)

        return SkillManifest(
            name=str(fm.get("name", path.parent.name)),
            description=str(fm.get("description", "")),
            version=str(fm.get("version", "0.1.0")),
            author=str(fm.get("author", "")),
            required_capabilities=caps,
            tags=list(fm.get("tags") or []),
            depends=list(fm.get("depends") or []),
            user_invocable=bool(fm.get("user_invocable", True)),
            disable_model_invocation=bool(fm.get("disable_model_invocation", False)),
            markdown_content=raw,
            metadata=dict(fm.get("metadata") or {}),
        )

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_frontmatter(raw: str) -> tuple[dict, str]:
        if not raw.startswith("---"):
            return {}, raw
        rest = raw[3:].lstrip("\n")
        end = rest.find("\n---")
        if end == -1:
            return {}, raw
        try:
            fm = yaml.safe_load(rest[:end]) or {}
        except yaml.YAMLError:
            fm = {}
        body = rest[end + 4:].lstrip("\n")
        return fm if isinstance(fm, dict) else {}, body


__all__ = ["SkillLoader", "detect_capabilities"]


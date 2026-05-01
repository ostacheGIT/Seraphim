from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import yaml

from seraphim.skills.parser import SkillParser
from seraphim.skills.sources.base import ResolvedSkill
from seraphim.skills.tool_translator import ToolTranslator

COPIED_SUBDIRS = ("references", "assets", "templates")


@dataclass(slots=True)
class ImportResult:
    success: bool = True
    skipped: bool = False
    target_path: Path | None = None
    translated_tools: List[str] = field(default_factory=list)
    untranslated_tools: List[str] = field(default_factory=list)
    scripts_imported: bool = False
    warnings: List[str] = field(default_factory=list)


class SkillImporter:
    def __init__(self, parser: SkillParser, tool_translator: ToolTranslator, target_root: Path | None = None) -> None:
        self._parser = parser
        self._translator = tool_translator
        self._target_root = Path(target_root or Path("~/.seraphim/skills/").expanduser())

    def import_skill(self, resolved: ResolvedSkill, *, with_scripts: bool = False, force: bool = False) -> ImportResult:
        result = ImportResult()
        target_dir = self._target_root / resolved.source / resolved.name
        result.target_path = target_dir

        if target_dir.exists() and not force:
            result.skipped = True
            result.warnings.append(f"Déjà installé dans {target_dir} (--force pour écraser)")
            return result

        source_md = resolved.path / "SKILL.md"
        if not source_md.exists():
            source_md = resolved.path / "skill.md"
            if not source_md.exists():
                result.success = False
                result.warnings.append(f"Aucun SKILL.md dans {resolved.path}")
                return result

        try:
            frontmatter, body = self._read_skill_md(source_md)
            self._parser.parse_frontmatter(frontmatter, markdown_content=body)
        except Exception as exc:
            result.success = False
            result.warnings.append(f"Erreur de parsing : {exc}")
            return result

        translated_body, untranslated = self._translator.translate_markdown(body)
        result.untranslated_tools = untranslated
        applied: List[str] = []
        for ext, internal in self._translator._table.items():
            if ext in body and ext not in translated_body:
                applied.append(f"{ext}->{internal}")
        result.translated_tools = applied

        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True)

        (target_dir / "SKILL.md").write_text(self._render_skill_md(frontmatter, translated_body), encoding="utf-8")

        for subdir in COPIED_SUBDIRS:
            src_sub = resolved.path / subdir
            if src_sub.exists():
                shutil.copytree(src_sub, target_dir / subdir)

        scripts_src = resolved.path / "scripts"
        if scripts_src.exists() and with_scripts:
            shutil.copytree(scripts_src, target_dir / "scripts")
            result.scripts_imported = True
        elif scripts_src.exists():
            result.warnings.append("scripts/ ignoré (--with-scripts pour l'inclure)")

        self._write_source_metadata(target_dir, resolved, result)
        return result

    def _read_skill_md(self, path: Path) -> tuple[dict, str]:
        raw = path.read_text(encoding="utf-8")
        if not raw.startswith("---"):
            return {}, raw
        rest = raw[3:].lstrip("\n")
        end = rest.find("\n---")
        if end == -1:
            return {}, raw
        fm_text = rest[:end]
        body = rest[end + 4:].lstrip("\n")
        try:
            fm = yaml.safe_load(fm_text)
            if not isinstance(fm, dict):
                fm = {}
        except yaml.YAMLError:
            fm = {}
        return fm, body

    def _render_skill_md(self, frontmatter: dict, body: str) -> str:
        fm_text = yaml.safe_dump(frontmatter, sort_keys=False, default_flow_style=False)
        return f"---\n{fm_text}---\n\n{body}"

    def _write_source_metadata(self, target_dir: Path, resolved: ResolvedSkill, result: ImportResult) -> None:
        installed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        translated_str = ", ".join(f'"{t}"' for t in result.translated_tools)
        missing_str = ", ".join(f'"{t}"' for t in result.untranslated_tools)
        content = (
            f'source = "{resolved.source}:{resolved.name}"\n'
            f'commit = "{resolved.commit}"\n'
            f'category = "{resolved.category}"\n'
            f'installed_at = "{installed_at}"\n'
            f"translated_tools = [{translated_str}]\n"
            f"missing_tools = [{missing_str}]\n"
            f"scripts_imported = {'true' if result.scripts_imported else 'false'}\n"
        )
        (target_dir / ".source").write_text(content, encoding="utf-8")


__all__ = ["ImportResult", "SkillImporter"]
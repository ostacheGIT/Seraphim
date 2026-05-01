from __future__ import annotations

import logging
import re
from typing import Any, Dict

from seraphim.skills.types import SkillManifest

LOGGER = logging.getLogger(__name__)

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_COMPATIBILITY_LENGTH = 500

SPEC_FIELDS = frozenset(
    {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
)

FIELD_MAPPING: Dict[str, tuple[str, str]] = {
    "version": ("field", "version"),
    "author": ("field", "author"),
    "tags": ("field", "tags"),
    "depends": ("field", "depends"),
    "required_capabilities": ("field", "required_capabilities"),
    "user_invocable": ("field", "user_invocable"),
    "disable_model_invocation": ("field", "disable_model_invocation"),
    "platforms": ("seraphim_meta", "platforms"),
    "prerequisites": ("seraphim_meta", "prerequisites"),
}


class SkillParseError(ValueError):
    pass


class SkillParser:
    def parse_frontmatter(self, frontmatter: Dict[str, Any], *, markdown_content: str = "") -> SkillManifest:
        self._validate_strict(frontmatter)
        return self._build_manifest(frontmatter, markdown_content)

    def _validate_strict(self, frontmatter: Dict[str, Any]) -> None:
        if "name" not in frontmatter:
            raise SkillParseError("Champ requis manquant : name")
        if "description" not in frontmatter:
            raise SkillParseError("Champ requis manquant : description")
        name = frontmatter["name"]
        description = frontmatter["description"]
        if not isinstance(name, str):
            raise SkillParseError(f"'name' doit être une chaîne")
        if not isinstance(description, str):
            raise SkillParseError(f"'description' doit être une chaîne")
        if len(name) == 0 or len(name) > MAX_NAME_LENGTH:
            raise SkillParseError(f"'name' doit faire 1-{MAX_NAME_LENGTH} caractères")
        if len(description) == 0 or len(description) > MAX_DESCRIPTION_LENGTH:
            raise SkillParseError(f"'description' doit faire 1-{MAX_DESCRIPTION_LENGTH} caractères")
        for ch in name:
            if not (ch.isalnum() or ch == "-"):
                raise SkillParseError(f"Caractère invalide '{ch}' dans le nom '{name}'")

    def _build_manifest(self, frontmatter: Dict[str, Any], markdown_content: str) -> SkillManifest:
        manifest = SkillManifest(
            name=frontmatter["name"],
            description=frontmatter["description"],
            markdown_content=markdown_content,
        )
        raw_metadata = frontmatter.get("metadata") or {}
        if not isinstance(raw_metadata, dict):
            raw_metadata = {}
        seraphim_meta = dict(raw_metadata.get("seraphim") or {})
        unmapped: Dict[str, Any] = {}
        for key, value in frontmatter.items():
            if key in SPEC_FIELDS:
                continue
            if key in FIELD_MAPPING:
                target, attr = FIELD_MAPPING[key]
                if target == "field":
                    setattr(manifest, attr, value)
                else:
                    seraphim_meta[attr] = value
            else:
                unmapped[key] = value
        if unmapped:
            seraphim_meta["original_frontmatter"] = unmapped
        if seraphim_meta:
            new_metadata = dict(raw_metadata)
            new_metadata["seraphim"] = seraphim_meta
            manifest.metadata = new_metadata
        else:
            manifest.metadata = raw_metadata
        return manifest


__all__ = ["SkillParseError", "SkillParser", "SPEC_FIELDS", "FIELD_MAPPING"]
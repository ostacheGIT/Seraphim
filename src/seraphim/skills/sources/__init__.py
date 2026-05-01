"""Résolveurs de sources de skills — Hermes, OpenClaw, GitHub générique."""

from seraphim.skills.sources.base import ResolvedSkill, SourceResolver
from seraphim.skills.sources.github import GitHubResolver
from seraphim.skills.sources.hermes import HERMES_REPO_URL, HermesResolver
from seraphim.skills.sources.openclaw import OPENCLAW_REPO_URL, OpenClawResolver

__all__ = [
    "GitHubResolver",
    "HERMES_REPO_URL",
    "HermesResolver",
    "OPENCLAW_REPO_URL",
    "OpenClawResolver",
    "ResolvedSkill",
    "SourceResolver",
]

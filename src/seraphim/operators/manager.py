"""OperatorManager — loads, saves, and runs OperatorManifests."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from seraphim.operators.manifest import OperatorManifest

logger = logging.getLogger(__name__)

_OPERATORS_DIR = Path.home() / ".seraphim" / "operators"
_EXTENSIONS = (".yaml", ".yml", ".json")


class OperatorManager:
    """Manages named operator manifests stored in ~/.seraphim/operators/.

    Operators bind a named agent to a specific configuration. They can be
    run by name, scheduled, and versioned as plain files.

    Usage:
        mgr = OperatorManager()
        mgr.save(OperatorManifest(name="digest", agent="react", schedule="08:00"))
        result = await mgr.run("digest", "Give me today's briefing")
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or _OPERATORS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def save(self, manifest: OperatorManifest) -> Path:
        """Persist an operator manifest to disk. Returns the saved path."""
        path = manifest.save(self._dir)
        logger.debug("Operator '%s' saved to %s", manifest.name, path)
        return path

    def load(self, name: str) -> OperatorManifest:
        """Load a manifest by name. Raises FileNotFoundError if not found."""
        for ext in _EXTENSIONS:
            path = self._dir / f"{name}{ext}"
            if path.exists():
                return OperatorManifest.from_file(path)
        raise FileNotFoundError(
            f"Operator '{name}' not found in {self._dir}. "
            f"Create it with OperatorManager().save(OperatorManifest(name='{name}', ...))"
        )

    def delete(self, name: str) -> bool:
        """Delete a manifest file. Returns True if deleted, False if not found."""
        for ext in _EXTENSIONS:
            path = self._dir / f"{name}{ext}"
            if path.exists():
                path.unlink()
                logger.info("Operator '%s' deleted", name)
                return True
        return False

    def list(self) -> list[OperatorManifest]:
        """Return all manifests found in the operators directory."""
        manifests: list[OperatorManifest] = []
        seen: set[str] = set()
        for ext in _EXTENSIONS:
            for path in sorted(self._dir.glob(f"*{ext}")):
                name = path.stem
                if name in seen:
                    continue
                seen.add(name)
                try:
                    manifests.append(OperatorManifest.from_file(path))
                except Exception as exc:
                    logger.warning("Failed to load operator '%s': %s", name, exc)
        return manifests

    def get(self, name: str) -> OperatorManifest | None:
        """Load a manifest by name, returning None if not found."""
        try:
            return self.load(name)
        except FileNotFoundError:
            return None

    # ── Execution ─────────────────────────────────────────────────────────────

    async def run(
        self,
        name: str,
        query: str,
        context: Any = None,
    ) -> str:
        """Instantiate the operator's agent and run the query.

        Applies system_prompt override if the manifest specifies one.
        """
        manifest = self.load(name)
        if not manifest.enabled:
            return f"Operator '{name}' is disabled."

        from seraphim.agents.base import get_agent
        agent = get_agent(manifest.agent)

        if manifest.system_prompt:
            try:
                agent.system_prompt = manifest.system_prompt
            except AttributeError:
                pass  # read-only property — injected via context in build_context

        logger.info("Running operator '%s' (agent=%s)", manifest.name, manifest.agent)
        return await agent.run(query, context)

    async def run_all_scheduled(self, current_time: str, query: str = "") -> dict[str, str]:
        """Run all enabled operators whose schedule matches current_time (HH:MM).

        Returns {operator_name: result}.
        """
        results: dict[str, str] = {}
        for manifest in self.list():
            if not manifest.enabled:
                continue
            if not manifest.schedule:
                continue
            # Simple HH:MM match
            if manifest.schedule.strip() == current_time.strip():
                try:
                    q = query or f"Run the {manifest.name} operator."
                    results[manifest.name] = await self.run(manifest.name, q)
                except Exception as exc:
                    results[manifest.name] = f"Error: {exc}"
        return results

    # ── Convenience ───────────────────────────────────────────────────────────

    def summary(self) -> str:
        """Human-readable table of all operators."""
        manifests = self.list()
        if not manifests:
            return "No operators defined. Create one with OperatorManager().save(...)"
        lines = ["Operators:", ""]
        for m in manifests:
            status = "✓" if m.enabled else "✗"
            sched = f"  [{m.schedule}]" if m.schedule else ""
            lines.append(f"  {status} {m.name:<20} agent={m.agent:<15}{sched}")
            if m.description:
                lines.append(f"      {m.description}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self.list())

    def __repr__(self) -> str:
        return f"<OperatorManager dir={self._dir} count={len(self)}>"


_default_manager: OperatorManager | None = None


def get_operator_manager() -> OperatorManager:
    """Return the singleton OperatorManager (lazy-initialized)."""
    global _default_manager
    if _default_manager is None:
        _default_manager = OperatorManager()
    return _default_manager


__all__ = ["OperatorManager", "get_operator_manager"]

"""Dependency graph — validation et union transitive de capabilities pour les skills.

Inspiré de OpenJarvis dependency.py.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List

from seraphim.skills.types import SkillManifest


@dataclass
class DependencyGraph:
    """Graphe orienté des dépendances entre skills."""

    manifests: Dict[str, SkillManifest]
    edges: Dict[str, List[str]] = field(default_factory=dict)  # name → dep list


def build_dependency_graph(manifests: Dict[str, SkillManifest]) -> DependencyGraph:
    """Construit le graphe depuis les manifestes.

    Les arêtes proviennent de:
    - manifest.depends (déclaratif)
    - step.skill_name dans les pipelines (implicite)
    """
    edges: Dict[str, List[str]] = {}
    for name, manifest in manifests.items():
        deps: List[str] = list(manifest.depends or [])
        for step in manifest.steps:
            if step.skill_name and step.skill_name not in deps:
                deps.append(step.skill_name)
        edges[name] = deps
    return DependencyGraph(manifests=manifests, edges=edges)


def validate_dependencies(
    graph: DependencyGraph,
    max_depth: int = 5,
) -> List[str]:
    """Valide le graphe — retourne la liste d'erreurs (vide = OK).

    Vérifie:
    - Dépendances manquantes
    - Cycles (Kahn's algorithm)
    - Profondeur > max_depth
    """
    errors: List[str] = []

    # 1. Dépendances manquantes
    for name, deps in graph.edges.items():
        for dep in deps:
            if dep not in graph.manifests:
                errors.append(
                    f"Skill '{name}' dépend de '{dep}' qui n'est pas installé."
                )

    # 2. Cycle detection — Kahn's algorithm
    nodes = set(graph.edges.keys())
    in_degree: Dict[str, int] = defaultdict(int)
    adj: Dict[str, List[str]] = defaultdict(list)

    for name, deps in graph.edges.items():
        for dep in deps:
            if dep in nodes:
                adj[dep].append(name)
                in_degree[name] += 1

    queue: deque = deque(n for n in nodes if in_degree[n] == 0)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for neighbor in adj[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited != len(nodes):
        remaining = sorted(n for n in nodes if in_degree[n] > 0)
        errors.append(
            f"Cycle de dépendances détecté parmi : {', '.join(remaining)}"
        )

    # 3. Profondeur maximale
    def _depth(name: str, path: frozenset) -> int:
        if name in path or name not in graph.edges:
            return 0
        path = path | {name}
        deps = [d for d in graph.edges[name] if d in graph.manifests]
        if not deps:
            return 0
        return 1 + max(_depth(d, path) for d in deps)

    for name in graph.manifests:
        d = _depth(name, frozenset())
        if d > max_depth:
            errors.append(
                f"Skill '{name}' — profondeur de dépendance {d} dépasse le max ({max_depth})."
            )

    return errors


def compute_capability_union(
    name: str,
    graph: DependencyGraph,
    _visited: frozenset = frozenset(),
) -> List[str]:
    """Union transitive des capabilities requises pour un skill et ses dépendances."""
    if name in _visited or name not in graph.manifests:
        return []

    _visited = _visited | {name}
    manifest = graph.manifests[name]
    caps: set[str] = set(manifest.required_capabilities or [])

    for dep in graph.edges.get(name, []):
        caps.update(compute_capability_union(dep, graph, _visited))

    return sorted(caps)


__all__ = [
    "DependencyGraph",
    "build_dependency_graph",
    "validate_dependencies",
    "compute_capability_union",
]

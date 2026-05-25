"""WorkflowLoader — loads workflow TOML files; WorkflowBuilder — fluent API."""

from __future__ import annotations

import sys
from pathlib import Path

from seraphim.workflow.graph import NodeType, WorkflowEdge, WorkflowGraph, WorkflowNode

_WORKFLOWS_ROOT = Path("~/.seraphim/workflows").expanduser()


def _read_toml(path: Path) -> dict:
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # type: ignore
    return tomllib.loads(path.read_bytes().decode())


class WorkflowLoader:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root or _WORKFLOWS_ROOT

    def load(self, name: str) -> WorkflowGraph:
        path = self._root / f"{name}.toml"
        if not path.exists():
            raise FileNotFoundError(f"Workflow '{name}' not found at {path}")
        return self._parse(_read_toml(path))

    def list_all(self) -> list[str]:
        if not self._root.exists():
            return []
        return [p.stem for p in sorted(self._root.glob("*.toml"))]

    def _parse(self, data: dict) -> WorkflowGraph:
        wf = data.get("workflow", {})
        graph = WorkflowGraph(wf.get("name", "unnamed"))
        for nd in wf.get("nodes", []):
            graph.add_node(WorkflowNode(
                id=nd["id"],
                type=NodeType(nd["type"]),
                config=nd.get("config", {}),
            ))
        for ed in wf.get("edges", []):
            graph.add_edge(WorkflowEdge(
                src=ed["src"],
                dst=ed["dst"],
                condition=ed.get("condition"),
            ))
        return graph


class WorkflowBuilder:
    def __init__(self, name: str) -> None:
        self._graph = WorkflowGraph(name)

    def add_agent(self, id: str, agent_name: str, query: str = "{input}", **cfg) -> "WorkflowBuilder":
        self._graph.add_node(WorkflowNode(
            id=id, type=NodeType.AGENT,
            config={"agent": agent_name, "query": query, **cfg},
        ))
        return self

    def add_tool(self, id: str, tool_name: str, **cfg) -> "WorkflowBuilder":
        self._graph.add_node(WorkflowNode(
            id=id, type=NodeType.TOOL,
            config={"tool": tool_name, **cfg},
        ))
        return self

    def add_condition(self, id: str, expression: str) -> "WorkflowBuilder":
        self._graph.add_node(WorkflowNode(
            id=id, type=NodeType.CONDITION,
            config={"expression": expression},
        ))
        return self

    def add_transform(self, id: str, template: str) -> "WorkflowBuilder":
        self._graph.add_node(WorkflowNode(
            id=id, type=NodeType.TRANSFORM,
            config={"template": template},
        ))
        return self

    def connect(self, src: str, dst: str, condition: str | None = None) -> "WorkflowBuilder":
        self._graph.add_edge(WorkflowEdge(src=src, dst=dst, condition=condition))
        return self

    def sequential(self, *ids: str) -> "WorkflowBuilder":
        for a, b in zip(ids, ids[1:]):
            self.connect(a, b)
        return self

    def build(self) -> WorkflowGraph:
        return self._graph


__all__ = ["WorkflowLoader", "WorkflowBuilder"]

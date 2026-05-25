"""WorkflowGraph — DAG of nodes with Kahn's topological sort."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum


class NodeType(str, Enum):
    AGENT     = "agent"
    TOOL      = "tool"
    CONDITION = "condition"
    PARALLEL  = "parallel"
    LOOP      = "loop"
    TRANSFORM = "transform"


@dataclass(slots=True)
class WorkflowNode:
    id: str
    type: NodeType
    config: dict = field(default_factory=dict)


@dataclass(slots=True)
class WorkflowEdge:
    src: str
    dst: str
    condition: str | None = None


class WorkflowGraph:
    def __init__(self, name: str) -> None:
        self.name = name
        self._nodes: dict[str, WorkflowNode] = {}
        self._edges: list[WorkflowEdge] = []

    def add_node(self, node: WorkflowNode) -> None:
        self._nodes[node.id] = node

    def add_edge(self, edge: WorkflowEdge) -> None:
        self._edges.append(edge)

    @property
    def nodes(self) -> dict[str, WorkflowNode]:
        return dict(self._nodes)

    @property
    def edges(self) -> list[WorkflowEdge]:
        return list(self._edges)

    def execution_stages(self) -> list[list[str]]:
        """Kahn's BFS topo sort — returns levels for asyncio.gather parallelism."""
        in_degree: dict[str, int] = {nid: 0 for nid in self._nodes}
        adj: dict[str, list[str]] = {nid: [] for nid in self._nodes}
        for edge in self._edges:
            if edge.src in adj and edge.dst in in_degree:
                adj[edge.src].append(edge.dst)
                in_degree[edge.dst] += 1

        queue: deque[str] = deque(nid for nid, deg in in_degree.items() if deg == 0)
        stages: list[list[str]] = []
        while queue:
            stage = list(queue)
            queue.clear()
            stages.append(stage)
            next_round: list[str] = []
            for nid in stage:
                for dst in adj[nid]:
                    in_degree[dst] -= 1
                    if in_degree[dst] == 0:
                        next_round.append(dst)
            queue.extend(next_round)
        return stages

    def validate(self) -> list[str]:
        errors: list[str] = []
        node_ids = set(self._nodes)

        for edge in self._edges:
            if edge.src not in node_ids:
                errors.append(f"Edge references unknown source node '{edge.src}'")
            if edge.dst not in node_ids:
                errors.append(f"Edge references unknown destination node '{edge.dst}'")

        # Cycle detection via DFS
        color: dict[str, str] = {nid: "white" for nid in node_ids}
        adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
        for edge in self._edges:
            if edge.src in adj:
                adj[edge.src].append(edge.dst)

        def _dfs(nid: str) -> bool:
            color[nid] = "gray"
            for dst in adj.get(nid, []):
                if dst not in color:
                    continue
                if color[dst] == "gray":
                    return True
                if color[dst] == "white" and _dfs(dst):
                    return True
            color[nid] = "black"
            return False

        for nid in node_ids:
            if color[nid] == "white" and _dfs(nid):
                errors.append(f"Cycle detected involving node '{nid}'")
                break

        return errors


__all__ = ["NodeType", "WorkflowNode", "WorkflowEdge", "WorkflowGraph"]

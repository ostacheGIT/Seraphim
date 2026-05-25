"""WorkflowEngine — executes a WorkflowGraph with asyncio parallelism."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from seraphim.workflow.graph import NodeType, WorkflowGraph, WorkflowNode

logger = logging.getLogger(__name__)


@dataclass
class WorkflowContext:
    inputs: dict[str, Any]
    outputs: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.outputs.get(key, self.inputs.get(key, default))

    def all_vars(self) -> dict[str, Any]:
        return {**self.inputs, **self.outputs}


class WorkflowEngine:
    def __init__(self, max_parallel: int = 4, timeout_secs: float = 300.0) -> None:
        self._max_parallel = max_parallel
        self._timeout = timeout_secs

    async def run(self, graph: WorkflowGraph, inputs: dict[str, Any]) -> WorkflowContext:
        ctx = WorkflowContext(inputs=dict(inputs))
        stages = graph.execution_stages()
        sem = asyncio.Semaphore(self._max_parallel)

        for stage in stages:
            tasks = [
                asyncio.create_task(self._run_node(graph.nodes[nid], ctx, sem))
                for nid in stage
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for nid, result in zip(stage, results):
                if isinstance(result, Exception):
                    logger.error("Node '%s' failed: %s", nid, result)
                    ctx.outputs[nid] = f"Error: {result}"
                else:
                    ctx.outputs[nid] = result

        return ctx

    async def _run_node(
        self, node: WorkflowNode, ctx: WorkflowContext, sem: asyncio.Semaphore
    ) -> str:
        async with sem:
            try:
                return await asyncio.wait_for(
                    self._dispatch(node, ctx),
                    timeout=self._timeout,
                )
            except asyncio.TimeoutError:
                return f"[timeout after {self._timeout}s]"

    async def _dispatch(self, node: WorkflowNode, ctx: WorkflowContext) -> str:
        ntype = NodeType(node.type)
        cfg = node.config
        all_vars = ctx.all_vars()

        if ntype == NodeType.AGENT:
            return await self._run_agent(cfg, all_vars)
        elif ntype == NodeType.TOOL:
            return await self._run_tool(cfg, all_vars)
        elif ntype == NodeType.CONDITION:
            return self._eval_condition(cfg, all_vars)
        elif ntype == NodeType.TRANSFORM:
            return self._apply_transform(cfg, all_vars)
        elif ntype == NodeType.LOOP:
            return await self._run_loop(node, cfg, ctx)
        else:
            return f"[unsupported node type: {ntype}]"

    async def _run_agent(self, cfg: dict, all_vars: dict) -> str:
        from seraphim.agents.core import BaseAgent
        import seraphim.agents  # ensure auto-registration

        agent_name = cfg.get("agent", "chat")
        query = _render(cfg.get("query", "{input}"), all_vars)
        agent_cls = BaseAgent._REGISTRY.get(agent_name) or BaseAgent._REGISTRY.get("chat")
        if agent_cls is None:
            return f"[no agent '{agent_name}']"
        return await agent_cls().run(query)

    async def _run_tool(self, cfg: dict, all_vars: dict) -> str:
        from seraphim.skills.registry import SKILL_REGISTRY, discover_skills
        if not SKILL_REGISTRY:
            discover_skills()

        tool_name = cfg.get("tool", "")
        skill = SKILL_REGISTRY.get(tool_name)
        if skill is None:
            return f"[tool '{tool_name}' not found]"

        rendered: dict[str, Any] = {
            k: _render(str(v), all_vars) if isinstance(v, str) else v
            for k, v in cfg.items()
            if k != "tool"
        }
        try:
            result = await skill.run(**rendered)
            return result.output if result.success else f"Error: {result.error}"
        except Exception as exc:
            return f"Error in {tool_name}: {exc}"

    def _eval_condition(self, cfg: dict, all_vars: dict) -> str:
        expr = _render(cfg.get("expression", "True"), all_vars)
        try:
            result = eval(expr, {"__builtins__": {}}, dict(all_vars))  # noqa: S307
            return "true" if result else "false"
        except Exception as exc:
            return f"[condition error: {exc}]"

    def _apply_transform(self, cfg: dict, all_vars: dict) -> str:
        return _render(cfg.get("template", "{input}"), all_vars)

    async def _run_loop(self, node: WorkflowNode, cfg: dict, ctx: WorkflowContext) -> str:
        max_iters = int(cfg.get("max_iterations", 3))
        body_type = NodeType(cfg.get("body_type", "agent"))
        body_cfg = cfg.get("body", {})
        last_output = ""
        for _ in range(max_iters):
            body_node = _InlineNode(node.id, body_type, body_cfg)
            last_output = await self._dispatch(body_node, ctx)
            stop_cond = cfg.get("stop_condition", "")
            if stop_cond and _render(stop_cond, ctx.all_vars()) == "true":
                break
        return last_output


class _InlineNode:
    __slots__ = ("id", "type", "config")

    def __init__(self, id: str, type: NodeType, config: dict) -> None:
        self.id = id
        self.type = type
        self.config = config


def _render(template: str, variables: dict[str, Any]) -> str:
    def _replace(m: re.Match) -> str:
        val = variables.get(m.group(1))
        return str(val) if val is not None else m.group(0)
    return re.sub(r"\{(\w+)\}", _replace, template)


__all__ = ["WorkflowContext", "WorkflowEngine"]

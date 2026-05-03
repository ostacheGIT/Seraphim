"""Trace collector — wraps agent runs to record steps and outcomes."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from seraphim.learning.trace_store import Trace, TraceStep, save_trace


class TraceCollector:
    """Accumulates steps during a single agent run."""

    def __init__(self, agent: str, query: str, session_id: str = "") -> None:
        self.trace = Trace(agent=agent, query=query, session_id=session_id, final_response="")
        self._start = time.monotonic()

    def record_step(self, tool: str, args: dict, output: str, latency_ms: float = 0.0) -> None:
        step = TraceStep(
            step=len(self.trace.steps),
            tool=tool,
            args=args,
            output=output[:2000],
            latency_ms=latency_ms,
        )
        self.trace.steps.append(step)

    def finish(self, response: str, success: bool = True) -> None:
        self.trace.final_response = response
        self.trace.success = success
        self.trace.latency_ms = (time.monotonic() - self._start) * 1000

    async def save(self) -> None:
        await save_trace(self.trace)


@asynccontextmanager
async def collect(agent: str, query: str, session_id: str = "") -> AsyncGenerator[TraceCollector, None]:
    """Context manager: auto-saves trace on exit regardless of success."""
    collector = TraceCollector(agent, query, session_id)
    try:
        yield collector
    except Exception as exc:
        collector.finish(response=f"ERROR: {exc}", success=False)
        await collector.save()
        raise
    else:
        if not collector.trace.final_response:
            collector.finish(response="", success=False)
        await collector.save()

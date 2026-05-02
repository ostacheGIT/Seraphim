"""monitor_operative daemon — asyncio background monitoring loop."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from seraphim.monitor.notifier import notify
from seraphim.monitor.store import init_db, list_monitors, update_check

log = logging.getLogger("seraphim.monitor")

_TRIGGER_WORDS = (
    "yes", "true", "oui", "triggered", "alert", "detected", "confirmed",
    "match", "found", "below", "above", "exceeded", "reached",
)

_STOP_EVENT: asyncio.Event | None = None


def _is_triggered(answer: str) -> bool:
    low = answer.lower()[:200]
    return any(w in low for w in _TRIGGER_WORDS)


async def _check_one(monitor: dict[str, Any]) -> None:
    name = monitor["name"]
    condition = monitor["condition"]
    action = monitor["action"]

    try:
        from seraphim.agents.base import ReActAgent
        from seraphim.agents.core import AgentContext

        agent = ReActAgent()
        ctx = AgentContext()
        ctx.add_system(
            "You are a monitoring agent. Evaluate the condition. "
            "Answer with YES if the condition is met, NO otherwise. "
            "Keep answer short."
        )
        prompt = (
            f"Condition to check: {condition}\n\n"
            "Use web_search or browser_search to gather current data if needed. "
            "Then answer YES or NO based on whether the condition is currently true."
        )
        result = await agent.run(prompt, ctx)
        triggered = _is_triggered(result)
        await update_check(name, result, triggered)

        if triggered:
            log.info("Monitor '%s' TRIGGERED: %s", name, result[:80])
            notify(f"Seraphim Monitor: {name}", result[:200])
            if action and action != "notify":
                log.info("Monitor '%s' action: %s", name, action)

    except Exception as exc:
        log.exception("Monitor '%s' check failed: %s", name, exc)
        await update_check(name, f"ERROR: {exc}", False)


async def _monitor_loop(monitor: dict[str, Any]) -> None:
    name = monitor["name"]
    interval = max(30, monitor["interval_secs"])
    log.info("Monitor '%s' started — interval %ss", name, interval)

    # Run first check immediately
    await _check_one(monitor)

    while not (_STOP_EVENT and _STOP_EVENT.is_set()):
        await asyncio.sleep(interval)
        # Reload in case interval/enabled changed
        from seraphim.monitor.store import get_monitor
        current = await get_monitor(name)
        if not current or not current["enabled"]:
            log.info("Monitor '%s' disabled — stopping loop", name)
            break
        await _check_one(current)


async def run_daemon(once: bool = False) -> None:
    """Run all enabled monitors. once=True: single pass, no loop."""
    global _STOP_EVENT
    _STOP_EVENT = asyncio.Event()

    await init_db()
    monitors = await list_monitors(enabled_only=True)

    if not monitors:
        log.info("No enabled monitors found.")
        print("[monitor] No enabled monitors. Add one with: seraphim monitor add")
        return

    print(f"[monitor] Starting {len(monitors)} monitor(s)...")
    for m in monitors:
        print(f"  • {m['name']} — every {m['interval_secs']}s")

    if once:
        tasks = [_check_one(m) for m in monitors]
        await asyncio.gather(*tasks)
        return

    tasks = [asyncio.create_task(_monitor_loop(m)) for m in monitors]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        _STOP_EVENT.set()


def stop_daemon() -> None:
    if _STOP_EVENT:
        _STOP_EVENT.set()

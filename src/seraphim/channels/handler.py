"""Channel message handler — routes incoming messages to the appropriate agent."""

from __future__ import annotations

import logging

from seraphim.channels.base import ChannelMessage

logger = logging.getLogger(__name__)


async def handle_channel_message(msg: ChannelMessage) -> str:
    from seraphim.agents.router import route
    from seraphim.agents.core import BaseAgent
    import seraphim.agents  # ensure agent auto-registration

    decision = route(msg.text)
    agent_cls = BaseAgent._REGISTRY.get(decision.agent) or BaseAgent._REGISTRY.get("chat")
    if agent_cls is None:
        return "[seraphim] No agent available."
    try:
        agent = agent_cls()
        return await agent.run(msg.text)
    except Exception as exc:
        logger.error("Channel handler error (channel=%s): %s", msg.channel, exc)
        return f"[seraphim] Error: {exc}"

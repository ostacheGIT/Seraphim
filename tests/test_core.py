"""
Seraphim — basic test suite.
"""

import pytest
from unittest.mock import AsyncMock, patch

from seraphim.agents.base import ChatAgent, CoderAgent, ResearcherAgent, get_agent, AgentContext
from seraphim.settings import SeraphimSettings


# ─── Settings ────────────────────────────────────────────────────────────────


def test_default_settings():
    s = SeraphimSettings()
    assert s.engine.provider == "ollama"
    assert s.engine.model == "llama3.2"
    assert s.server.port == 7272


# ─── Agent registry ──────────────────────────────────────────────────────────


def test_get_agent_chat():
    agent = get_agent("chat")
    assert isinstance(agent, ChatAgent)


def test_get_agent_coder():
    agent = get_agent("coder")
    assert isinstance(agent, CoderAgent)


def test_get_agent_researcher():
    agent = get_agent("researcher")
    assert isinstance(agent, ResearcherAgent)


def test_get_agent_unknown():
    with pytest.raises(ValueError, match="Unknown agent"):
        get_agent("doesnt_exist")


# ─── AgentContext ─────────────────────────────────────────────────────────────


def test_agent_context_messages():
    ctx = AgentContext()
    ctx.add_system("You are helpful.")
    ctx.add_user("Hello!")
    ctx.add_assistant("Hi!")

    assert ctx.messages[0]["role"] == "system"
    assert ctx.messages[1]["role"] == "user"
    assert ctx.messages[2]["role"] == "assistant"


# ─── Agent run (mocked engine) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_agent_run():
    agent = ChatAgent()
    agent.engine = AsyncMock()
    agent.engine.chat = AsyncMock(return_value="Hello, I'm Seraphim!")

    response = await agent.run("Say hello")
    assert "Seraphim" in response
    agent.engine.chat.assert_called_once()


@pytest.mark.asyncio
async def test_coder_agent_run():
    agent = CoderAgent()
    agent.engine = AsyncMock()
    agent.engine.chat = AsyncMock(return_value="```python\nprint('hello')\n```")

    response = await agent.run("Write hello world in Python")
    assert "python" in response.lower()

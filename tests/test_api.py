"""Tests for FastAPI routes."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with patch("seraphim.memory.store.init_db", new_callable=AsyncMock), \
         patch("seraphim.memory.init_rag", return_value=None):
        from seraphim.api.app import app
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


# ─── Public routes ────────────────────────────────────────────────────────────

def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Seraphim"
    assert data["status"] == "running"


def test_health_engine_unreachable(client):
    with patch("seraphim.api.app.get_engine") as mock_engine_factory:
        mock_engine = MagicMock()
        mock_engine.chat = AsyncMock(side_effect=Exception("connection refused"))
        mock_engine_factory.return_value = mock_engine
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["engine"] == "unreachable"


def test_list_models(client):
    r = client.get("/models")
    assert r.status_code == 200
    models = r.json()["models"]
    assert any("3b" in m["id"].lower() for m in models)
    assert any("7b" in m["id"].lower() for m in models)


def test_list_agents(client):
    r = client.get("/agents")
    assert r.status_code == 200
    agents = {a["name"] for a in r.json()["agents"]}
    assert "chat" in agents
    assert "react" in agents


# ─── Auth ─────────────────────────────────────────────────────────────────────

def test_chat_requires_api_key_when_configured(client):
    with patch("seraphim.api.app.settings") as mock_settings:
        mock_settings.server.api_key = "secret-key"
        mock_settings.server.cors_origins = ["*"]
        r = client.post("/chat", json={"query": "hello"})
    assert r.status_code == 403


def test_chat_accepts_valid_api_key(client):
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(return_value="test response")

    with patch("seraphim.api.app.settings") as mock_settings, \
         patch("seraphim.api.app._build_agent", return_value=mock_agent), \
         patch("seraphim.api.app._resolve_agent_name", return_value="chat"), \
         patch("seraphim.api.app.save_message", new_callable=AsyncMock):
        mock_settings.server.api_key = "secret-key"
        mock_settings.server.cors_origins = ["*"]
        r = client.post(
            "/chat",
            json={"query": "hello"},
            headers={"X-API-Key": "secret-key"},
        )
    assert r.status_code in (200, 422)


def test_chat_no_auth_when_key_empty(client):
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(return_value="ok")

    with patch("seraphim.api.app._build_agent", return_value=mock_agent), \
         patch("seraphim.api.app._resolve_agent_name", return_value="chat"), \
         patch("seraphim.api.app.save_message", new_callable=AsyncMock), \
         patch("seraphim.api.app.settings") as mock_settings:
        mock_settings.server.api_key = ""
        mock_settings.server.cors_origins = ["*"]
        r = client.post("/chat", json={"query": "hello"})
    assert r.status_code != 403


# ─── Memory routes ────────────────────────────────────────────────────────────

def test_get_sessions_empty(client):
    with patch("seraphim.api.app.list_sessions", new_callable=AsyncMock, return_value=[]):
        r = client.get("/memory/sessions")
    assert r.status_code == 200
    assert r.json() == []


def test_get_session_history(client):
    fake_msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    with patch("seraphim.api.app.load_history", new_callable=AsyncMock, return_value=fake_msgs):
        r = client.get("/memory/sessions/test-session-id")
    assert r.status_code == 200
    data = r.json()
    assert data["session"] == "test-session-id"
    assert len(data["messages"]) == 2

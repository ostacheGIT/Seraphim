"""Tests for LLM engine layer."""

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from seraphim.engine.base import ChatMessage, ChatResult
from seraphim.engine.ollama import OllamaEngine
from seraphim.engine import get_engine


def test_get_engine_default():
    engine = get_engine()
    assert engine is not None


def test_get_engine_qwen3b():
    engine = get_engine("ollama_qwen3b")
    assert isinstance(engine, OllamaEngine)
    assert "3b" in engine.model.lower() or "3b" in engine.model


def test_get_engine_qwen7b():
    engine = get_engine("ollama_qwen7b")
    assert isinstance(engine, OllamaEngine)
    assert "7b" in engine.model.lower() or "7b" in engine.model


def test_get_engine_unknown():
    with pytest.raises(KeyError):
        get_engine("nonexistent_engine_xyz")


def test_chat_message_structure():
    msg: ChatMessage = {"role": "user", "content": "hello"}
    assert msg["role"] == "user"
    assert msg["content"] == "hello"


@pytest.mark.asyncio
async def test_ollama_engine_chat_success():
    engine = OllamaEngine(model="qwen2.5:3b", base_url="http://localhost:11434")
    mock_response = MagicMock()
    mock_response.json.return_value = {"response": "Hello from Ollama!"}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        result = await engine.chat([{"role": "user", "content": "hi"}])

    assert "messages" in result
    assert len(result["messages"]) == 1
    assert result["messages"][0]["role"] == "assistant"
    assert result["messages"][0]["content"] == "Hello from Ollama!"


@pytest.mark.asyncio
async def test_ollama_engine_builds_prompt_correctly():
    engine = OllamaEngine()
    captured_payload = {}

    async def fake_post(url, json=None, **kwargs):
        captured_payload.update(json or {})
        mock = MagicMock()
        mock.json.return_value = {"response": "ok"}
        mock.raise_for_status = MagicMock()
        return mock

    with patch("httpx.AsyncClient.post", side_effect=fake_post):
        await engine.chat([
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "test"},
        ])

    assert "[SYSTEM]" in captured_payload["prompt"]
    assert "[USER]" in captured_payload["prompt"]


@pytest.mark.asyncio
async def test_ollama_engine_http_error_propagates():
    engine = OllamaEngine()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ConnectError("Connection refused")
        with pytest.raises(httpx.ConnectError):
            await engine.chat([{"role": "user", "content": "hi"}])

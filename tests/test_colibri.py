"""Moteur colibri : testé contre un faux serveur OpenAI-compatible (httpx.MockTransport)."""

import json

import httpx
import pytest

from seraphim.engine import colibri as colibri_mod
from seraphim.engine.colibri import ColibriEngine, ColibriUnavailable


def _fake_server(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(colibri_mod.httpx, "AsyncClient", factory)


def _handler(seen: list):
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "inkling2-mini-iq2"}]})
        body = json.loads(request.content)
        if body.get("stream"):
            events = [
                {"choices": [{"delta": {"reasoning_content": "je réfléchis"}}]},
                {"choices": [{"delta": {"content": "Bon"}}]},
                {"choices": [{"delta": {"content": "jour"}}]},
            ]
            text = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
        msg = {"role": "assistant", "content": ""}
        if body.get("tools"):
            msg["tool_calls"] = [{"id": "1", "type": "function",
                                  "function": {"name": "calculator", "arguments": "{\"expr\": \"2+2\"}"}}]
        else:
            msg["content"] = "Salut"
        return httpx.Response(200, json={"choices": [{"message": msg}], "usage": {"total_tokens": 3}})
    return handle


async def test_discovers_model_and_streams_answer_without_reasoning(monkeypatch):
    seen: list = []
    _fake_server(monkeypatch, _handler(seen))
    eng = ColibriEngine(base_url="http://127.0.0.1:8000/v1", autostart=False)

    out = "".join([c async for c in eng.stream_chat_api([{"role": "user", "content": "hi"}])])

    assert out == "Bonjour"
    assert eng.model == "inkling2-mini-iq2"
    sent = json.loads(seen[-1].content)
    assert sent["model"] == "inkling2-mini-iq2" and sent["stream"] is True
    assert seen[-1].url.path == "/v1/chat/completions"       # pas de /v1/v1


async def test_chat_passes_tool_calls_through(monkeypatch):
    _fake_server(monkeypatch, _handler([]))
    eng = ColibriEngine(autostart=False)

    res = await eng.chat([{"role": "user", "content": "2+2 ?"}],
                         tools=[{"type": "function", "function": {"name": "calculator"}}])

    call = res["messages"][-1]["tool_calls"][0]
    assert call["function"]["name"] == "calculator"
    assert json.loads(call["function"]["arguments"]) == {"expr": "2+2"}


async def test_plain_chat_and_api_key(monkeypatch):
    seen: list = []
    _fake_server(monkeypatch, _handler(seen))
    eng = ColibriEngine(autostart=False, api_key="local-secret")

    res = await eng.chat([{"role": "user", "content": "salut"}])

    assert res["messages"][-1]["content"] == "Salut"
    assert seen[-1].headers["authorization"] == "Bearer local-secret"


async def test_unavailable_without_autostart(monkeypatch):
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    _fake_server(monkeypatch, down)
    eng = ColibriEngine(autostart=False, home="")

    with pytest.raises(ColibriUnavailable):
        await eng.chat([{"role": "user", "content": "hi"}])

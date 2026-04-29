"""
Seraphim API — FastAPI application.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from seraphim import __version__
from seraphim.agents.base import AGENT_REGISTRY, AgentContext, get_agent
from seraphim.engine.ollama import engine
from seraphim.settings import settings
from seraphim.memory.store import init_db, load_history, list_sessions, delete_session
from seraphim.voice.speaker import synthesize_to_bytes, synthesize_stream, speak_async
import asyncio
from functools import partial

app = FastAPI(
    title="Seraphim",
    description="Your personal AI, running entirely on your machine",
    version=__version__,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Schemas ─────────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    query: str
    agent: str = "chat"
    model: str | None = None
    messages: list[dict[str, str]] = []
    stream: bool = False


class ChatResponse(BaseModel):
    response: str
    agent: str
    model: str


class TTSRequest(BaseModel):
    text: str


# ─── Routes ──────────────────────────────────────────────────────────────────


@app.get("/")
async def root():
    return {
        "name": "Seraphim",
        "version": __version__,
        "status": "running",
    }


@app.get("/health")
async def health():
    ollama_ok = await engine.health_check()
    return {
        "seraphim": "ok",
        "ollama": "ok" if ollama_ok else "unreachable",
        "model": settings.engine.model,
    }


@app.get("/models")
async def list_models():
    try:
        models = await engine.list_models()
        return {"models": models, "default": settings.engine.model}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Ollama unavailable: {e}")


@app.get("/agents")
async def list_agents():
    return {
        "agents": [
            {"name": name, "description": cls.description}
            for name, cls in AGENT_REGISTRY.items()
        ]
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        ag = get_agent(req.agent)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if req.model:
        settings.engine.model = req.model

    ctx = AgentContext(messages=req.messages) if req.messages else None
    response = await ag.run(req.query, ctx)

    return ChatResponse(
        response=response,
        agent=req.agent,
        model=settings.engine.model,
    )


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    try:
        ag = get_agent(req.agent)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    ctx = AgentContext(messages=req.messages) if req.messages else AgentContext()
    ctx.add_system(ag.system_prompt)
    ctx.add_user(req.query)

    async def token_generator():
        async for token in engine.stream_chat(ctx.messages, model=req.model):
            yield token

    return StreamingResponse(token_generator(), media_type="text/plain")


# ─── Memory ──────────────────────────────────────────────────────────────────


@app.get("/memory/sessions")
async def get_sessions():
    await init_db()
    sessions = await list_sessions()
    return [
        {
            "session_id": s if isinstance(s, str) else s["session_id"],
            "title": s.get("title", s["session_id"]) if isinstance(s, dict) else s,
            "updated_at": s.get("updated_at", None) if isinstance(s, dict) else None,
        }
        for s in sessions
    ]


@app.get("/memory/sessions/{session_id}")
async def get_session_history(session_id: str, limit: int = 20):
    await init_db()
    messages = await load_history(session_id, limit=limit)
    return {"session": session_id, "messages": messages}


@app.delete("/memory/sessions/{session_id}")
async def remove_session(session_id: str):
    await init_db()
    await delete_session(session_id)
    return {"deleted": session_id}


# ─── TTS / Voice ─────────────────────────────────────────────────────────────

@app.post("/tts/speak")
async def tts_speak(req: TTSRequest):
    """Joue la voix JARVIS directement sur la machine locale (non bloquant)."""
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")
    speak_async(req.text)
    return {"status": "speaking", "text": req.text}


@app.post("/tts/audio")
async def tts_audio(req: TTSRequest):
    """Retourne le WAV complet — attend la fin de la synthèse."""
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")
    loop = asyncio.get_event_loop()
    audio_bytes = await loop.run_in_executor(
        None,
        partial(synthesize_to_bytes, req.text)
    )
    return StreamingResponse(
        iter([audio_bytes]),
        media_type="audio/wav",
        headers={"Content-Disposition": "inline; filename=response.wav"},
    )


@app.post("/tts/stream")
async def tts_stream(req: TTSRequest):
    """Stream PCM brut chunk par chunk — latence réduite, audio démarre immédiatement."""
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")

    def generate():
        for chunk in synthesize_stream(req.text):
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="audio/pcm",
        headers={
            "X-Sample-Rate": str(24000),
            "X-Channels": "1",
            "X-Bit-Depth": "16",
        },
    )
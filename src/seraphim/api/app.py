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

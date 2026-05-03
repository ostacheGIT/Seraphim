"""
Seraphim API — FastAPI application.
"""

import logging
import uuid
import asyncio
from functools import partial
from typing import Optional, Literal

logger = logging.getLogger(__name__)

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

from seraphim import __version__
from seraphim.agents.base import AGENT_REGISTRY, AgentContext, get_agent
from seraphim.agents.router import route as auto_route
from seraphim.engine import get_engine
from seraphim.settings import settings
from seraphim.memory.store import (
    init_db,
    load_history,
    list_sessions,
    delete_session,
    save_message,
)
from seraphim.learning.trace_store import (
    save_trace,
    Trace as LearningTrace,
    set_feedback,
)
from seraphim.voice.speaker import synthesize_to_bytes, speak_async

app = FastAPI(
    title="Seraphim",
    description="Your personal AI, running entirely on your machine",
    version=__version__,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.server.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Session-Id", "X-Engine-Id", "X-Routed-Agent", "X-Trace-Id"],
)

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _require_api_key(x_api_key: str | None = Security(_api_key_header)) -> None:
    configured = settings.server.api_key
    if not configured:
        return
    if x_api_key != configured:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")

# ─── Startup ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    await init_db()
    from seraphim.memory import init_rag
    init_rag()
    _maybe_start_daemon()


def _maybe_start_daemon() -> None:
    """Auto-start learning daemon if config has auto_start=true and daemon not running."""
    import json
    import subprocess
    import sys
    from pathlib import Path
    from seraphim.learning.daemon import CONFIG_FILE, PID_FILE, is_alive

    if not CONFIG_FILE.exists():
        return
    try:
        config = json.loads(CONFIG_FILE.read_text())
    except Exception:
        return
    if not config.get("auto_start"):
        return

    if PID_FILE.exists():
        try:
            if is_alive(int(PID_FILE.read_text().strip())):
                return  # already running
        except (ValueError, Exception):
            pass
        PID_FILE.unlink(missing_ok=True)

    from seraphim.learning.daemon import LOG_FILE
    log_file = open(LOG_FILE, "a")
    if sys.platform == "win32":
        from pathlib import Path as _Path
        pythonw = _Path(sys.executable).parent / "pythonw.exe"
        executable = str(pythonw) if pythonw.exists() else sys.executable
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        subprocess.Popen(
            [executable, "-m", "seraphim.learning.daemon"],
            stdout=log_file, stderr=log_file,
            creationflags=flags,
        )
    else:
        subprocess.Popen(
            [sys.executable, "-m", "seraphim.learning.daemon"],
            stdout=log_file, stderr=log_file,
            start_new_session=True,
        )
    logger.info("Learning daemon auto-started.")


# ─── Schemas ─────────────────────────────────────────────────────────────────

EngineId = Optional[Literal["ollama_qwen3b", "ollama_qwen7b"]]


class ChatRequest(BaseModel):
    query: str
    # "auto" (défaut) = le router choisit automatiquement
    agent: str = "auto"
    model: str | None = None
    engine_id: EngineId = None
    session_id: str | None = None
    messages: list[dict[str, str]] = []
    stream: bool = False


class ChatResponse(BaseModel):
    response: str
    agent: str
    engine_id: str
    session_id: str
    routed_agent: str   # agent réellement utilisé après routing
    trace_id: str


class FeedbackRequest(BaseModel):
    trace_id: str
    score: float  # 0.0 = mauvais, 1.0 = bon


class TTSRequest(BaseModel):
    text: str


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"name": "Seraphim", "version": __version__, "status": "running"}


@app.get("/health")
async def health():
    try:
        engine = get_engine()
        _ = await engine.chat(
            messages=[{"role": "user", "content": "ping"}],
        )
        engine_status = "ok"
    except Exception:
        logger.warning("Health check: engine unreachable", exc_info=True)
        engine_status = "unreachable"

    return {
        "seraphim": "ok",
        "engine": engine_status,
        "default_engine_id": "ollama_qwen3b",
    }


@app.get("/models")
async def list_models():
    return {
        "models": [
            {"id": "ollama_qwen3b", "label": "Qwen 2.5 3B (rapide)"},
            {"id": "ollama_qwen7b", "label": "Qwen 2.5 7B (plus précis)"},
        ],
        "default": "ollama_qwen3b",
    }


@app.get("/agents")
async def list_agents():
    return {
        "agents": [
            {"name": name, "description": cls.description}
            for name, cls in AGENT_REGISTRY.items()
        ]
    }


@app.get("/skills")
async def list_installed_skills():
    from pathlib import Path
    skills_root = Path("~/.seraphim/skills").expanduser()
    skills = []
    if skills_root.exists():
        for skill_md in sorted(skills_root.rglob("SKILL.md")):
            name = skill_md.parent.name
            source = skill_md.parent.parent.name
            try:
                import yaml
                raw = skill_md.read_text(encoding="utf-8")
                if raw.startswith("---"):
                    rest = raw[3:].lstrip("\n")
                    end = rest.find("\n---")
                    fm = yaml.safe_load(rest[:end]) if end != -1 else {}
                    description = fm.get("description", "") if isinstance(fm, dict) else ""
                else:
                    description = ""
            except Exception:
                logger.debug("Failed to parse skill manifest for %s", name, exc_info=True)
                description = ""
            skills.append({
                "id": f"skill:{name}",
                "name": name,
                "source": source,
                "description": description,
            })
    return {"skills": skills}


@app.get("/skills/native")
async def list_native_skills():
    import importlib
    from seraphim.skills.base import BaseSkill

    _SKILL_MODULES = [
        "seraphim.skills.core.calculator",
        "seraphim.skills.core.think",
        "seraphim.skills.core.code_interpreter",
        "seraphim.skills.core.shell",
        "seraphim.skills.core.http_request",
        "seraphim.skills.core.repl",
        "seraphim.skills.core.digest",
        "seraphim.skills.web.search",
        "seraphim.skills.web.browser",
        "seraphim.skills.system.control",
        "seraphim.skills.system.files",
        "seraphim.skills.system.screen",
        "seraphim.skills.memory.sqlite",
    ]

    seen: dict = {}
    for mod_name in _SKILL_MODULES:
        try:
            mod = importlib.import_module(mod_name)
            for attr in dir(mod):
                obj = getattr(mod, attr)
                if (isinstance(obj, type)
                        and issubclass(obj, BaseSkill)
                        and obj is not BaseSkill):
                    try:
                        instance = obj()
                        if instance.name not in seen:
                            seen[instance.name] = instance
                    except Exception as exc:
                        logger.warning("Cannot instantiate %s: %s", attr, exc)
        except Exception as exc:
            logger.warning("Cannot import skill module %s: %s", mod_name, exc)

    skills = [
        {
            "id": f"skill:{name}",
            "name": name,
            "source": "native",
            "description": getattr(skill, "description", ""),
        }
        for name, skill in sorted(seen.items())
    ]
    return {"skills": skills}


@app.get("/skills/catalog")
async def search_skill_catalog(q: str = "", limit: int = 200, offset: int = 0, source: str = ""):
    from seraphim.skills.catalog import search_skills, list_catalog, get_catalog_size
    if q.strip():
        results = search_skills(q.strip(), top_k=limit)
        if source:
            results = [r for r in results if r.get("source") == source]
    else:
        results = list_catalog(limit=limit, offset=offset, source=source)
    return {"skills": results, "catalog_size": get_catalog_size()}


class SkillInstallRequest(BaseModel):
    name: str
    source: str = "hermes"
    force: bool = False


@app.post("/skills/install", dependencies=[Depends(_require_api_key)])
async def install_skill_endpoint(req: SkillInstallRequest):
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, partial(_do_install_skill, req.name, req.source, req.force))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return result


def _do_install_skill(name: str, source: str, force: bool) -> dict:
    """
    Install a skill from cache by slug+source, without going through a resolver.
    Works for every source (hermes, openclaw, leoye, skillssh, autonomys, voltagent…).
    """
    from pathlib import Path as _Path
    from seraphim.skills.parser import SkillParser
    from seraphim.skills.tool_translator import ToolTranslator
    from seraphim.skills.importer import SkillImporter
    from seraphim.skills.sources.base import ResolvedSkill
    import subprocess

    if source == "installed":
        raise ValueError("Skill déjà installé")

    cache_base = _Path("~/.seraphim/skill-cache").expanduser() / source
    if not cache_base.exists():
        raise ValueError(f"Cache source '{source}' introuvable dans ~/.seraphim/skill-cache/")

    # Find skill directory by slug — search all SKILL.md under the source cache
    skill_dir: _Path | None = None
    for skill_md in cache_base.rglob("SKILL.md"):
        if skill_md.parent.name == name:
            skill_dir = skill_md.parent
            break

    if skill_dir is None:
        raise ValueError(f"Skill '{name}' introuvable dans le cache {source}")

    # Read commit hash if available
    commit = ""
    git_dir = cache_base / ".git"
    if git_dir.exists():
        try:
            r = subprocess.run(
                ["git", "-C", str(cache_base), "rev-parse", "HEAD"],
                capture_output=True, text=True,
            )
            commit = r.stdout.strip()
        except Exception:
            pass

    resolved = ResolvedSkill(
        name=name,
        source=source,
        path=skill_dir,
        category=skill_dir.parent.name,
        description="",
        commit=commit,
    )

    importer = SkillImporter(parser=SkillParser(), tool_translator=ToolTranslator())
    res = importer.import_skill(resolved, force=force)

    return {
        "success": res.success,
        "skipped": res.skipped,
        "skill_name": name,
        "source": source,
        "warnings": res.warnings,
    }


@app.post("/skills/catalog/build", dependencies=[Depends(_require_api_key)])
async def build_skill_catalog():
    loop = asyncio.get_event_loop()
    count = await loop.run_in_executor(None, _do_build_catalog)
    return {"indexed": count}


def _do_build_catalog() -> int:
    from seraphim.skills.catalog import build_catalog
    return build_catalog()


def _resolve_engine_id(req: ChatRequest) -> str:
    engine_id: str | None = req.engine_id
    if engine_id is None and req.model:
        if "7b" in req.model:
            engine_id = "ollama_qwen7b"
        else:
            engine_id = "ollama_qwen3b"
    return engine_id or "ollama_qwen3b"


def _resolve_agent_name(req: ChatRequest) -> str:
    """
    Si agent == "auto" (ou non fourni), le router choisit automatiquement.
    Sinon on respecte le choix explicite.
    """
    if req.agent in ("auto", "", None):
        decision = auto_route(req.query)
        return decision.agent
    return req.agent


def _build_agent(agent_name: str, engine_id: str):
    _ = get_engine(engine_id)
    ag = get_agent(agent_name)
    if hasattr(ag, "engine_id"):
        ag.engine_id = engine_id
    return ag


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(_require_api_key)])
async def chat(req: ChatRequest):
    engine_id = _resolve_engine_id(req)
    routed_agent = _resolve_agent_name(req)

    try:
        ag = _build_agent(routed_agent, engine_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    session_id = req.session_id or str(uuid.uuid4())
    if req.messages:
        ctx = AgentContext(messages=req.messages)
    elif req.session_id:
        history = await load_history(req.session_id, limit=20)
        ctx = AgentContext(messages=history)
    else:
        ctx = None
    response = await ag.run(req.query, ctx)

    await save_message(session_id, "user", req.query, routed_agent)
    await save_message(session_id, "assistant", response, routed_agent)

    trace_id = str(uuid.uuid4())
    await save_trace(LearningTrace(
        id=trace_id,
        agent=routed_agent,
        query=req.query,
        final_response=response,
        session_id=session_id,
        tokens_in=len(req.query) // 4,
        tokens_out=len(response) // 4,
    ))

    return ChatResponse(
        response=response,
        agent=req.agent,
        engine_id=engine_id,
        session_id=session_id,
        routed_agent=routed_agent,
        trace_id=trace_id,
    )


@app.post("/chat/stream", dependencies=[Depends(_require_api_key)])
async def chat_stream(req: ChatRequest):
    engine_id = _resolve_engine_id(req)
    routed_agent = _resolve_agent_name(req)

    try:
        ag = _build_agent(routed_agent, engine_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    session_id = req.session_id or str(uuid.uuid4())
    if req.messages:
        ctx = AgentContext(messages=req.messages)
    elif req.session_id:
        history = await load_history(req.session_id, limit=20)
        ctx = AgentContext(messages=history)
    else:
        ctx = AgentContext()

    result = await ag.run(req.query, ctx)

    await save_message(session_id, "user", req.query, routed_agent)
    await save_message(session_id, "assistant", result, routed_agent)

    trace_id = str(uuid.uuid4())
    await save_trace(LearningTrace(
        id=trace_id,
        agent=routed_agent,
        query=req.query,
        final_response=result,
        session_id=session_id,
        tokens_in=len(req.query) // 4,
        tokens_out=len(result) // 4,
    ))

    async def generator():
        yield result

    from fastapi.responses import StreamingResponse as SR
    response = SR(generator(), media_type="text/plain")
    response.headers["X-Session-Id"] = session_id
    response.headers["X-Engine-Id"] = engine_id
    response.headers["X-Routed-Agent"] = routed_agent
    response.headers["X-Trace-Id"] = trace_id
    return response


# ─── Feedback ────────────────────────────────────────────────────────────────

@app.post("/feedback", dependencies=[Depends(_require_api_key)])
async def submit_feedback(req: FeedbackRequest):
    score = max(0.0, min(1.0, req.score))
    await set_feedback(req.trace_id, score)
    return {"ok": True}


# ─── Memory ──────────────────────────────────────────────────────────────────

@app.get("/memory/sessions")
async def get_sessions():
    sessions = await list_sessions()
    return [
        {
            "session_id": s["session"],
            "title": s["preview"] or s["session"],
            "agent": s["agent"],
            "updated_at": s["timestamp"],
        }
        for s in sessions
    ]


@app.get("/memory/sessions/{session_id}")
async def get_session_history(session_id: str, limit: int = 50):
    messages = await load_history(session_id, limit=limit)
    return {"session": session_id, "messages": messages}


@app.delete("/memory/sessions/{session_id}")
async def remove_session(session_id: str):
    await delete_session(session_id)
    return {"deleted": session_id}


# ─── TTS / Voice ─────────────────────────────────────────────────────────────

@app.post("/tts/speak", dependencies=[Depends(_require_api_key)])
async def tts_speak(req: TTSRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")
    speak_async(req.text)
    return {"status": "speaking", "text": req.text}


@app.post("/tts/audio", dependencies=[Depends(_require_api_key)])
async def tts_audio(req: TTSRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")
    loop = asyncio.get_event_loop()
    audio_bytes = await loop.run_in_executor(
        None, partial(synthesize_to_bytes, req.text)
    )
    return StreamingResponse(
        iter([audio_bytes]),
        media_type="audio/wav",
        headers={"Content-Disposition": "inline; filename=response.wav"},
    )
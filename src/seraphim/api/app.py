"""
Seraphim API — FastAPI application.
"""

import json
import logging
import os
import re
import tempfile
import uuid
import asyncio
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Optional, Literal

logger = logging.getLogger(__name__)

from fastapi import Depends, FastAPI, File, HTTPException, Request, Security, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

from seraphim import __version__
from seraphim.agents.base import AGENT_REGISTRY, AgentContext, get_agent
from seraphim.agents.router import route as auto_route
from seraphim.engine import get_engine, get_default_engine_id
from seraphim.settings import settings
from seraphim.memory.store import (
    init_db,
    load_history,
    load_history_with_summary,
    list_sessions,
    search_sessions,
    delete_session,
    truncate_session,
    save_message,
    save_session_title,
    get_session_message_count,
    load_older_messages_for_summary,
    save_session_summary,
)
from seraphim.memory.user_facts import (
    get_all_facts,
    save_fact,
    delete_fact,
    search_facts,
    format_facts_for_prompt,
)
from seraphim.learning.trace_store import (
    save_trace,
    Trace as LearningTrace,
    set_feedback,
)
from seraphim.voice.speaker import synthesize_to_bytes, speak_async

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    from seraphim.memory import create_backend, set_rag_backend
    set_rag_backend(create_backend("sqlite_fts"))
    _maybe_start_daemon()
    yield


app = FastAPI(
    title="Seraphim",
    description="Your personal AI, running entirely on your machine",
    version=__version__,
    lifespan=lifespan,
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

def _maybe_start_daemon() -> None:
    """Auto-start learning daemon if config has auto_start=true and daemon not running."""
    import json
    import subprocess
    import sys
    from pathlib import Path
    from seraphim.learning.daemon import CONFIG_FILE, PID_FILE, is_alive

    lc = settings.learning
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

    if CONFIG_FILE.exists():
        try:
            existing = json.loads(CONFIG_FILE.read_text())
            # Sync auto_start from YAML if YAML says true but file says false
            if lc.auto_start and not existing.get("auto_start"):
                existing["auto_start"] = True
                CONFIG_FILE.write_text(json.dumps(existing, indent=2))
        except Exception:
            pass
    elif lc.auto_start:
        CONFIG_FILE.write_text(json.dumps({
            "auto_start": True,
            "interval_hours": lc.interval_hours,
            "min_new_traces": lc.min_new_traces,
            "min_quality": lc.min_quality,
            "run_grpo": lc.run_grpo,
            "run_finetune": lc.run_finetune,
            "agents": "react,chat",
        }, indent=2))

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
    with open(LOG_FILE, "a") as log_file:
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

EngineId = Optional[Literal["auto", "ollama_qwen3b", "ollama_qwen7b", "openai", "mistral", "claude", "vllm", "llamacpp"]]

# ─── Complexity-based auto-router (3B ↔ 7B) ──────────────────────────────────

_COMPLEX_RE = re.compile(
    r"\b("
    # French
    r"analys|expliqu|implement|débogu|refactoris|compar|différen|pourquoi|comment fonctionne|"
    r"étape par étape|crée un|construis|conçoi|algorithme|optimis|résumé|traduis|génère|"
    r"écris un|rédige|essay|rapport|thèse|argument|démontr|prouv|"
    # English
    r"analyze|analyz|explain|implement|debug|refactor|compar|difference|why|how does|"
    r"step.by.step|write a|create a|build|design|architect|algorithm|optimiz|optimis|"
    r"review|critique|essay|report|summari|translat|convert|formula|equation|calcul|"
    r"proof|theorem|generate|draft"
    r")\b",
    re.IGNORECASE,
)
_AUTO_LONG_THRESHOLD = 120  # words above this → 7B


def _complexity_route(query: str) -> str:
    """Return the better engine_id for a given query."""
    if len(query.split()) >= _AUTO_LONG_THRESHOLD:
        return "ollama_qwen7b"
    if _COMPLEX_RE.search(query):
        return "ollama_qwen7b"
    return "ollama_qwen3b"


# ── Language detection ────────────────────────────────────────────────────────

_FR_MARKERS = re.compile(
    r"\b(?:je|tu|il|elle|nous|vous|ils|elles|"
    r"le|la|les|un|une|des|du|au|aux|"
    r"est|sont|était|c'est|qu'|j'|"
    r"pourquoi|comment|quand|combien|"
    r"merci|bonjour|salut|oui|non|"
    r"avec|pour|dans|sur|sous|vers|chez|"
    r"mais|donc|ainsi|car|puis|aussi)\b",
    re.IGNORECASE,
)
_EN_MARKERS = re.compile(
    r"\b(?:the|and|that|this|with|have|from|"
    r"what|when|where|how|why|who|which|"
    r"i'm|i've|i'll|i'd|it's|don't|can't|won't|isn't|"
    r"please|hello|hi|thanks|thank|yes|no|"
    r"but|also|because|so|then|just|"
    r"you|your|we|our|they|their)\b",
    re.IGNORECASE,
)


def _detect_language(text: str) -> str:
    """Heuristic language detection returning 'fr', 'en', or 'auto'."""
    sample = text[:300]
    fr = len(_FR_MARKERS.findall(sample))
    en = len(_EN_MARKERS.findall(sample))
    # Accented chars are a strong French/Romance indicator
    fr += sum(1 for c in sample if c in "éèêëàâçîïôûùüœæÉÈÊËÀÂÇÎÏÔÛÙÜŒÆ")
    if fr > en + 2:
        return "fr"
    if en > fr + 2:
        return "en"
    return "auto"


# ── Sliding summary buffer ────────────────────────────────────────────────────

_SUMMARY_THRESHOLD = 30   # total messages before we start summarising
_KEEP_RECENT = 20         # messages kept verbatim; older ones are summarised


async def _generate_session_summary(session_id: str) -> None:
    """Background: summarise older messages and persist the result."""
    total = await get_session_message_count(session_id)
    if total < _SUMMARY_THRESHOLD:
        return

    older = await load_older_messages_for_summary(session_id, keep_recent=_KEEP_RECENT)
    if not older:
        return

    # Build a condensed transcript (cap to avoid blowing the context)
    transcript = "\n".join(
        f"{m['role'].upper()}: {(m.get('content') or '')[:150]}"
        for m in older[-30:]
    )
    prompt = (
        "Summarize the following conversation history in 3-5 concise bullet points. "
        "Focus on key topics, decisions, and facts established. Max 200 words.\n\n"
        + transcript
    )

    try:
        from seraphim.engine import get_engine
        eng = get_engine("ollama_qwen3b")
        result = await eng.chat([{"role": "user", "content": prompt}])
        msgs = result.get("messages", [])
        summary = (msgs[-1].get("content", "") if msgs else "").strip()
        if summary:
            await save_session_summary(session_id, summary, total)
    except Exception as exc:
        logger.debug("Summary generation failed for session %s: %s", session_id, exc)


async def _maybe_summarize_session(session_id: str) -> None:
    total = await get_session_message_count(session_id)
    if total >= _SUMMARY_THRESHOLD:
        await _generate_session_summary(session_id)


class ChatRequest(BaseModel):
    query: str
    # "auto" (défaut) = le router choisit automatiquement
    agent: str = "auto"
    model: str | None = None
    engine_id: EngineId = None
    session_id: str | None = None
    messages: list[dict[str, str]] = []
    stream: bool = False
    image: str | None = None  # base64 PNG/JPEG — described by llava before passing to agent


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


# ─── Image helper ────────────────────────────────────────────────────────────

_VISION_MODELS = {"llava", "llava-phi3", "bakllava", "moondream", "minicpm-v", "llava-llama3"}


def _find_vision_model(base_url: str) -> str | None:
    """Return first installed vision-capable model name, or None."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=5) as r:
            data = json.loads(r.read())
        for m in data.get("models", []):
            name = m.get("name", "").split(":")[0].lower()
            if name in _VISION_MODELS:
                return m["name"]
    except Exception:
        pass
    return None


def _ocr_image_b64(image_b64: str) -> str:
    """Run Windows WinRT OCR on a base64 image. Returns extracted text or ''."""
    import base64
    import subprocess
    import tempfile
    import time
    from pathlib import Path

    _PS_OCR = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$absPath = (Resolve-Path $env:SERAPHIM_OCR_IMG).Path
[void][System.Reflection.Assembly]::LoadWithPartialName("System.Runtime.WindowsRuntime")
$null = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics,ContentType=WindowsRuntime]
function Await($Task) {
    $methods = [System.WindowsRuntimeSystemExtensions].GetMethods()
    $asTask  = $methods | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and !$_.IsGenericMethod } | Select-Object -First 1
    $net = $asTask.Invoke($null, @($Task))
    $net.Wait(-1) | Out-Null
    $net.Result
}
$file    = Await([Windows.Storage.StorageFile]::GetFileFromPathAsync($absPath))
$stream  = Await($file.OpenAsync([Windows.Storage.FileAccessMode]::Read))
$decoder = Await([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream))
$bitmap  = Await($decoder.GetSoftwareBitmapAsync())
$engine  = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { exit 1 }
$result  = Await($engine.RecognizeAsync($bitmap))
Write-Output $result.Text
"""
    tmp = Path(tempfile.gettempdir()) / f"seraphim_ocr_{int(time.time())}.png"
    try:
        tmp.write_bytes(base64.b64decode(image_b64))
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_OCR],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            env={**os.environ, "SERAPHIM_OCR_IMG": str(tmp)},
        )
        return (proc.stdout or "").strip()
    except Exception as e:
        logger.debug("OCR on pasted image failed: %s", e)
        return ""
    finally:
        tmp.unlink(missing_ok=True)


async def _describe_image(image_b64: str, user_query: str) -> str | None:
    """OCR + optional vision model description. Returns None if no vision model."""
    import json
    import urllib.request
    base_url = settings.engine.base_url.rstrip("/")

    loop = asyncio.get_running_loop()

    # Always run OCR — deterministic, accurate for text
    ocr_text = await loop.run_in_executor(None, _ocr_image_b64, image_b64)

    vision_model = await loop.run_in_executor(None, _find_vision_model, base_url)
    if not vision_model and not ocr_text:
        return None  # nothing to work with

    if not vision_model:
        # OCR only — enough for text-heavy screenshots
        return f"[OCR text from image]\n{ocr_text}" if ocr_text else None

    payload = json.dumps({
        "model": vision_model,
        "prompt": (
            "Describe this image concisely. "
            "List visible UI elements, text, icons, and layout. "
            "Be factual, do not invent names or URLs not clearly visible."
        ),
        "images": [image_b64],
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"{base_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    def _call():
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())
    try:
        visual = await loop.run_in_executor(None, _call)
        visual_desc = visual.get("response", "").strip()
    except Exception as e:
        logger.warning("Vision model call failed: %s", e)
        visual_desc = ""

    parts = []
    if ocr_text:
        parts.append(f"[Texte extrait par OCR — exact]\n{ocr_text}")
    if visual_desc:
        parts.append(f"[Description visuelle]\n{visual_desc}")
    return "\n\n".join(parts) if parts else None


_NO_VISION_MSG = (
    "⚠️ **Aucun modèle de vision installé.**\n\n"
    "Pour analyser des images, installez llava :\n"
    "```\nollama pull llava\n```\n"
    "Puis relancez Seraphim."
)


async def _augment_query_with_image(query: str, image_b64: str | None) -> tuple[str, bool]:
    """Returns (augmented_query, vision_unavailable).
    vision_unavailable=True when image provided but no vision model installed."""
    if not image_b64:
        return query, False
    description = await _describe_image(image_b64, query)
    if description is None:
        return query, True
    return (
        f"{description}\n\n"
        f"---\n"
        f"{query}"
    ), False


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
        "default_engine_id": get_default_engine_id() or "ollama_qwen3b",
    }


@app.post("/engine/warmup")
async def engine_warmup(body: dict):
    """Pre-load a model into Ollama memory so the first real request is fast."""
    engine_id = body.get("engine_id", "ollama_qwen3b")
    # "auto" resolves to 3B for warmup purposes
    if engine_id == "auto":
        engine_id = "ollama_qwen3b"
    # Skip warmup for non-Ollama engines (cloud APIs don't need it)
    if engine_id not in ("ollama_qwen3b", "ollama_qwen7b"):
        return {"status": "skipped", "engine_id": engine_id}
    try:
        eng = get_engine(engine_id)
        async for _ in eng.stream_chat_api([{"role": "user", "content": "hi"}]):
            pass
        return {"status": "ready", "engine_id": engine_id}
    except Exception as exc:
        return {"status": "error", "engine_id": engine_id, "detail": str(exc)}


@app.get("/models")
async def list_models():
    return {
        "models": [
            {"id": "ollama_qwen3b", "label": "Qwen 2.5 3B (rapide)"},
            {"id": "ollama_qwen7b", "label": "Qwen 2.5 7B (plus précis)"},
        ],
        "default": "ollama_qwen3b",
    }


@app.get("/engines")
async def list_engines():
    """Return all engines (Ollama always present; external shown configured or not)."""
    from seraphim.engine import list_available_engines
    return {"engines": list_available_engines()}


@app.get("/engines/keys")
async def get_engine_keys():
    """Return which external engines have an API key configured (never returns the key itself)."""
    from seraphim.engine import get_external_keys_status
    return {"keys": get_external_keys_status()}


class EngineKeyRequest(BaseModel):
    engine: str   # "openai" | "mistral" | "claude"
    key: str      # empty string to remove the key


@app.post("/engines/keys", dependencies=[Depends(_require_api_key)])
async def set_engine_key(req: EngineKeyRequest):
    """Save (or clear) an external API key and immediately register/unregister the engine."""
    if req.engine not in ("openai", "mistral", "claude"):
        raise HTTPException(status_code=400, detail=f"Unknown engine: {req.engine}")
    from seraphim.engine import update_external_key
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: update_external_key(req.engine, req.key.strip()))
    return {"ok": True, "engine": req.engine, "configured": bool(req.key.strip())}


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
        "seraphim.skills.documents.pdf_reader",
        "seraphim.skills.documents.meeting_notes",
        "seraphim.skills.documents.csv_analyst",
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
    loop = asyncio.get_running_loop()
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
    loop = asyncio.get_running_loop()
    count = await loop.run_in_executor(None, _do_build_catalog)
    return {"indexed": count}


def _do_build_catalog() -> int:
    from seraphim.skills.catalog import build_catalog
    return build_catalog()


def _resolve_engine_id(req: ChatRequest) -> str:
    engine_id: str | None = req.engine_id
    if engine_id in (None, "auto"):
        if req.model and not engine_id:
            engine_id = "ollama_qwen7b" if "7b" in req.model else "ollama_qwen3b"
        else:
            engine_id = _complexity_route(req.query)
    return engine_id or "ollama_qwen3b"


async def _get_engine_metrics(engine_id: str):
    """Return last InferenceMetrics from the engine if it supports it."""
    try:
        from seraphim.engine.metrics import InferenceMetrics
        eng = get_engine(engine_id)
        m = getattr(eng, "last_metrics", None)
        if isinstance(m, InferenceMetrics):
            return m
    except Exception:
        pass
    return None


async def _resolve_agent_name(req: ChatRequest) -> str:
    """
    Si agent == "auto" (ou non fourni), le router choisit automatiquement.
    Le learned router peut override le choix statique si assez de traces.
    Sinon on respecte le choix explicite.
    """
    if req.agent in ("auto", "", None):
        decision = auto_route(req.query)
        if decision.agent == "chat":
            # Only let learned_route override when static is uncertain (chat)
            try:
                from seraphim.agents.learned_router import learned_route
                override = await learned_route(req.query, decision.agent)
                if override:
                    logger.debug("Learned router override: %s → %s (%s)",
                                 decision.agent, override.agent, override.reason)
                    return override.agent
            except Exception:
                pass
        return decision.agent
    return req.agent


def _build_agent(agent_name: str, engine_id: str):
    _ = get_engine(engine_id)
    ag = get_agent(agent_name)
    if hasattr(ag, "engine_id"):
        ag.engine_id = engine_id
    return ag


async def _enrich_agent_context(
    ag, query: str, session_summary: str | None = None
) -> None:
    """Inject summary, user facts, RAG context, and language hint into agent system prompt."""
    parts: list[str] = []

    # Sliding summary of older conversation turns
    if session_summary:
        parts.append(f"[Summary of earlier conversation]\n{session_summary}")

    # User facts
    facts = await get_all_facts()
    facts_text = format_facts_for_prompt(facts)
    if facts_text:
        parts.append(facts_text)

    # RAG context
    from seraphim.memory import get_rag_backend
    from seraphim.memory.context import format_context
    rag_backend = get_rag_backend()
    if rag_backend:
        try:
            results = rag_backend.retrieve(query, top_k=5)
            if results:
                parts.append("Documents pertinents de la base de connaissances:\n\n" + format_context(results))
        except Exception as exc:
            logger.debug("RAG retrieval failed: %s", exc)

    # Language instruction — always explicit to avoid small-model confusion
    lang = _detect_language(query)
    if lang == "en":
        parts.append("The user is writing in English. Always reply in English.")
    elif lang == "fr":
        parts.append("L'utilisateur écrit en français. Réponds TOUJOURS en français.")

    if parts:
        ag.system_prompt = ag.system_prompt + "\n\n" + "\n\n".join(parts)


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(_require_api_key)])
async def chat(req: ChatRequest):
    engine_id = _resolve_engine_id(req)
    routed_agent = await _resolve_agent_name(req)

    try:
        ag = _build_agent(routed_agent, engine_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    session_id = req.session_id or str(uuid.uuid4())
    if req.messages:
        ctx = AgentContext(messages=req.messages, session_id=session_id)
    elif req.session_id:
        history = await load_history(req.session_id, limit=20)
        ctx = AgentContext(messages=history, session_id=session_id)
    else:
        ctx = AgentContext(session_id=session_id)
    effective_query, vision_unavailable = await _augment_query_with_image(req.query, req.image)
    if vision_unavailable:
        response = _NO_VISION_MSG
    else:
        response = await ag.run(effective_query, ctx)

    await save_message(session_id, "user", req.query, routed_agent)
    await save_message(session_id, "assistant", response, routed_agent)

    # Grab inference metrics from the engine if available
    inf = await _get_engine_metrics(engine_id)

    trace_id = str(uuid.uuid4())
    await save_trace(LearningTrace(
        id=trace_id,
        agent=routed_agent,
        query=req.query,
        final_response=response,
        session_id=session_id,
        tokens_in=inf.tokens_in if inf else len(req.query) // 4,
        tokens_out=inf.tokens_out if inf else len(response) // 4,
        ttft_ms=inf.ttft_ms if inf else 0.0,
        throughput_tps=inf.throughput_tps if inf else 0.0,
        gpu_util_pct=inf.gpu_util_pct if inf else 0.0,
        vram_used_mb=inf.vram_used_mb if inf else 0.0,
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
async def chat_stream(req: ChatRequest, request: Request):
    engine_id = _resolve_engine_id(req)
    session_id = req.session_id or str(uuid.uuid4())
    session_summary: str | None = None

    # Resolve agent routing and load session history in parallel when both are needed
    if req.session_id and not req.messages:
        results = await asyncio.gather(
            _resolve_agent_name(req),
            load_history_with_summary(req.session_id, keep_recent=_KEEP_RECENT),
        )
        routed_agent = results[0]
        history, session_summary = results[1]
        ctx = AgentContext(messages=history, session_id=session_id)
    else:
        routed_agent = await _resolve_agent_name(req)
        ctx = AgentContext(messages=req.messages, session_id=session_id) if req.messages else AgentContext(session_id=session_id)

    try:
        ag = _build_agent(routed_agent, engine_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    effective_query, vision_unavailable = await _augment_query_with_image(req.query, req.image)
    await _enrich_agent_context(ag, req.query, session_summary=session_summary)
    trace_id = str(uuid.uuid4())

    async def generator():
        import httpx as _httpx
        result = ""
        actual_engine_id = engine_id

        async def _stream_primary() -> list[str]:
            chunks: list[str] = []
            async for chunk in ag.stream(effective_query, ctx):
                chunks.append(chunk)
                yield chunk

        async def _stream_fallback(fb_engine_id: str) -> list[str]:
            """Simple chat with the fallback external engine (no agent tools)."""
            fb_eng = get_engine(fb_engine_id)
            system_prompt = getattr(ag, "system_prompt", "")
            msgs: list[dict] = []
            if system_prompt:
                msgs.append({"role": "system", "content": system_prompt})
            msgs.extend(ctx.messages)
            msgs.append({"role": "user", "content": effective_query})
            async for chunk in fb_eng.stream_chat_api(msgs):
                yield chunk

        try:
            if vision_unavailable:
                result = _NO_VISION_MSG
                yield result
            elif req.image:
                from seraphim.agents.core import AgentContext as _AC
                chat_ag = _build_agent("chat", engine_id)
                img_ctx = _AC()
                img_ctx.add_system(chat_ag.system_prompt)
                img_ctx.add_user(effective_query)
                try:
                    result = await chat_ag._chat(img_ctx.messages)
                except Exception as exc:
                    result = f"⚠️ Erreur LLM : {exc}"
                yield result
            else:
                chunks: list[str] = []
                try:
                    _i = 0
                    async for chunk in ag.stream(effective_query, ctx):
                        chunks.append(chunk)
                        yield chunk
                        _i += 1
                        if _i % 8 == 0 and await request.is_disconnected():
                            logger.debug("Client disconnected — stopping stream for session %s", session_id)
                            result = "".join(chunks)
                            return
                except (_httpx.ConnectError, ConnectionRefusedError, OSError) as exc:
                    # Primary Ollama unreachable — try fallback if configured and no output yet
                    from seraphim.settings import settings as _s
                    fb = _s.external_api.fallback_engine if _s.external_api.fallback_enabled else ""
                    if fb and not chunks:
                        try:
                            actual_engine_id = fb
                            logger.warning("Ollama unavailable (%s) — falling back to %s", exc, fb)
                            notice = f"[Ollama indisponible — réponse via {fb}]\n\n"
                            yield notice
                            chunks.append(notice)
                            async for chunk in _stream_fallback(fb):
                                chunks.append(chunk)
                                yield chunk
                        except Exception as fb_exc:
                            err = f"\n⚠️ Fallback {fb} échoué : {fb_exc}"
                            chunks.append(err)
                            yield err
                    else:
                        error_chunk = f"\n⚠️ Moteur indisponible : {exc}"
                        chunks.append(error_chunk)
                        yield error_chunk
                except Exception as exc:
                    error_chunk = f"\n⚠️ Erreur moteur LLM : {exc}"
                    chunks.append(error_chunk)
                    yield error_chunk
                result = "".join(chunks)
        finally:
            inf = await _get_engine_metrics(actual_engine_id)
            await asyncio.gather(
                save_message(session_id, "user", req.query, routed_agent),
                save_message(session_id, "assistant", result, routed_agent),
                save_trace(LearningTrace(
                    id=trace_id,
                    agent=routed_agent,
                    query=req.query,
                    final_response=result,
                    session_id=session_id,
                    tokens_in=inf.tokens_in if inf else len(req.query) // 4,
                    tokens_out=inf.tokens_out if inf else len(result) // 4,
                    ttft_ms=inf.ttft_ms if inf else 0.0,
                    throughput_tps=inf.throughput_tps if inf else 0.0,
                    gpu_util_pct=inf.gpu_util_pct if inf else 0.0,
                    vram_used_mb=inf.vram_used_mb if inf else 0.0,
                )),
            )
            asyncio.create_task(_extract_user_facts_background(req.query))
            asyncio.create_task(_maybe_summarize_session(session_id))

    from fastapi.responses import StreamingResponse as SR
    response = SR(generator(), media_type="text/plain")
    response.headers["X-Session-Id"]  = session_id
    response.headers["X-Engine-Id"]   = engine_id   # resolved engine (may be 3B or 7B when auto)
    response.headers["X-Routed-Agent"] = routed_agent
    response.headers["X-Trace-Id"]    = trace_id
    return response


# ─── Feedback ────────────────────────────────────────────────────────────────

@app.post("/feedback", dependencies=[Depends(_require_api_key)])
async def submit_feedback(req: FeedbackRequest):
    score = max(0.0, min(1.0, req.score))
    await set_feedback(req.trace_id, score)
    return {"ok": True}


# ─── Learning ────────────────────────────────────────────────────────────────

@app.get("/learning/status")
async def learning_status():
    from seraphim.learning.daemon import read_state, PID_FILE, is_alive
    state = read_state()
    pid = state.get("pid")
    running = False
    if pid:
        try:
            running = is_alive(int(pid))
        except Exception:
            pass
    return {"running": running, **state}


@app.get("/learning/metrics")
async def learning_metrics():
    from seraphim.learning.trace_store import trace_stats
    return await trace_stats()


@app.post("/learning/trigger", dependencies=[Depends(_require_api_key)])
async def learning_trigger():
    """Trigger a learning run in the background. Writes progress to daemon state file."""
    import asyncio
    import datetime
    from seraphim.learning.orchestrator import LearningConfig, run_learning_loop
    from seraphim.learning.daemon import read_state, _write_state

    async def _run():
        state = read_state()
        state["status"] = "training"
        state["last_run"] = datetime.datetime.now().isoformat()
        _write_state(state)
        try:
            result = await run_learning_loop(LearningConfig())
            last_result = {
                "at": datetime.datetime.now().isoformat(),
                "mined": result.mined_pairs,
                "accepted": result.accepted,
                "rejected": result.rejected,
                "grpo_pairs": result.grpo_result.get("pairs_saved", 0) if result.grpo_result else 0,
                "error": None,
            }
        except Exception as exc:
            logger.error("Learning trigger failed: %s", exc)
            last_result = {"at": datetime.datetime.now().isoformat(), "error": str(exc)}

        state = read_state()
        state["status"] = "idle"
        state["last_result"] = last_result
        _write_state(state)

    asyncio.create_task(_run())
    return {"ok": True, "running": True}


@app.post("/learning/daemon/start", dependencies=[Depends(_require_api_key)])
async def learning_daemon_start():
    _maybe_start_daemon()
    return {"ok": True}


@app.post("/learning/daemon/stop", dependencies=[Depends(_require_api_key)])
async def learning_daemon_stop():
    import sys
    from seraphim.learning.daemon import PID_FILE, is_alive
    if not PID_FILE.exists():
        return {"ok": True}
    try:
        pid = int(PID_FILE.read_text().strip())
        if is_alive(pid):
            if sys.platform == "win32":
                import subprocess
                subprocess.call(["taskkill", "/F", "/PID", str(pid)], stdout=-1, stderr=-1)
            else:
                import os, signal as _signal
                os.kill(pid, _signal.SIGTERM)
        PID_FILE.unlink(missing_ok=True)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


# ─── Memory ──────────────────────────────────────────────────────────────────

def _fmt_sessions(sessions: list[dict]) -> list[dict]:
    return [
        {
            "session_id": s["session"],
            "title":      s["preview"] or s["session"],
            "agent":      s["agent"],
            "updated_at": s["timestamp"],
        }
        for s in sessions
    ]


@app.get("/memory/sessions")
async def get_sessions():
    return _fmt_sessions(await list_sessions())


@app.get("/memory/search")
async def search_memory(q: str = ""):
    if not q.strip():
        return _fmt_sessions(await list_sessions())
    return _fmt_sessions(await search_sessions(q.strip()))


@app.get("/memory/sessions/{session_id}")
async def get_session_history(session_id: str, limit: int = 50):
    messages = await load_history(session_id, limit=limit)
    return {"session": session_id, "messages": messages}


@app.delete("/memory/sessions/{session_id}")
async def remove_session(session_id: str):
    await delete_session(session_id)
    return {"deleted": session_id}


class TruncateRequest(BaseModel):
    keep_count: int


@app.post("/memory/sessions/{session_id}/truncate", dependencies=[Depends(_require_api_key)])
async def truncate_session_endpoint(session_id: str, req: TruncateRequest):
    await truncate_session(session_id, req.keep_count)
    return {"ok": True, "session_id": session_id, "kept": req.keep_count}


class TitleRequest(BaseModel):
    text: Optional[str] = None


@app.post("/memory/sessions/{session_id}/title")
async def generate_session_title(session_id: str, req: TitleRequest | None = None):
    """Génère un titre court via LLM pour la session et le persiste.

    Si `text` est fourni dans le body, on l'utilise directement (pas de lecture DB),
    ce qui permet d'appeler l'endpoint en parallèle du streaming principal.
    """
    text = req.text if req else None
    fallback = text[:40] + ("..." if text and len(text) > 40 else "") if text else session_id

    if text:
        context = f"user: {text[:400]}"
    else:
        messages = await load_history(session_id, limit=4)
        if not messages:
            return {"title": session_id}
        context = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in messages[:2])
        fallback = next((m["content"][:40] for m in messages if m["role"] == "user"), session_id)

    prompt = [{
        "role": "user",
        "content": (
            f"Génère un titre court (3-5 mots, sans guillemets ni ponctuation finale) "
            f"qui résume cette conversation:\n\n{context}\n\nTitre:"
        ),
    }]

    engine = get_engine()
    parts: list[str] = []
    try:
        if hasattr(engine, "stream_chat_api"):
            async for chunk in engine.stream_chat_api(prompt):
                parts.append(chunk)
                if len("".join(parts)) > 80:
                    break
        else:
            result = await engine.chat(prompt)
            msgs = result.get("messages", [])
            parts = [msgs[-1].get("content", "") if msgs else ""]
    except Exception:
        pass

    title = "".join(parts).strip().split("\n")[0].strip("\"'").strip()
    if not title or len(title) > 60:
        title = fallback

    await save_session_title(session_id, title)
    return {"title": title}


# ─── Long-term memory: auto-extraction ───────────────────────────────────────

_PERSONA_KEYWORDS = frozenset([
    "je ", "j'ai", "j'aime", "j'habite", "je vis", "je suis", "je m'appelle",
    "mon ", "ma ", "mes ", "moi,", "moi.", "moi ", "nous ", "notre ",
    "i am", "i have", "i like", "i live", "i work", "i'm ", "my ", "i call",
    "me llamo", "soy ", "vivo ", "tengo ", "mi ",
])


async def _extract_user_facts_background(query: str) -> None:
    """Extract user facts from a message and persist them (fire-and-forget)."""
    if len(query.strip()) < 20:
        return
    q_lower = query.lower()
    if not any(kw in q_lower for kw in _PERSONA_KEYWORDS):
        return

    prompt = [{
        "role": "user",
        "content": (
            "Tu es un extracteur de faits. Analyse ce message et extrait les faits CERTAINS "
            "et EXPLICITES sur l'utilisateur (prénom, âge, ville, profession, hobby, etc.).\n"
            "Réponds UNIQUEMENT avec un JSON {\"clé\": \"valeur\"} ou {} si rien à extraire.\n"
            "N'invente rien. N'interprète pas. Extrait seulement ce qui est dit clairement.\n\n"
            f"Message: {query[:600]}\n\nJSON:"
        ),
    }]

    engine = get_engine()
    try:
        raw_parts: list[str] = []
        if hasattr(engine, "stream_chat_api"):
            async for chunk in engine.stream_chat_api(prompt):
                raw_parts.append(chunk)
                if sum(len(p) for p in raw_parts) > 400:
                    break
        else:
            result = await engine.chat(prompt)
            msgs = result.get("messages", [])
            raw_parts = [msgs[-1].get("content", "") if msgs else ""]

        raw = "".join(raw_parts).strip()
        start, end = raw.find("{"), raw.rfind("}") + 1
        if 0 <= start < end:
            facts = json.loads(raw[start:end])
            if isinstance(facts, dict):
                for k, v in facts.items():
                    if isinstance(k, str) and isinstance(v, (str, int, float)) and str(v).strip():
                        await save_fact(k.strip(), str(v).strip())
                        logger.debug("Auto-extracted fact: %s = %s", k, v)
    except Exception as exc:
        logger.debug("Fact extraction skipped: %s", exc)


# ─── User Facts ──────────────────────────────────────────────────────────────

@app.get("/memory/facts")
async def get_user_facts():
    facts = await get_all_facts()
    return {"facts": facts}


class FactRequest(BaseModel):
    key: str
    value: str


@app.post("/memory/facts")
async def set_user_fact(req: FactRequest):
    await save_fact(req.key, req.value)
    return {"ok": True, "key": req.key}


@app.delete("/memory/facts/{key}")
async def remove_user_fact(key: str):
    deleted = await delete_fact(key)
    return {"ok": deleted, "key": key}


# ─── RAG ─────────────────────────────────────────────────────────────────────

class RAGIngestRequest(BaseModel):
    content: str
    source: str = "manual"


@app.post("/rag/ingest")
async def rag_ingest(req: RAGIngestRequest):
    from seraphim.memory import get_rag_backend, ingest_text
    backend = get_rag_backend()
    if backend is None:
        raise HTTPException(status_code=503, detail="RAG backend not initialized")
    loop = asyncio.get_running_loop()
    ids = await loop.run_in_executor(
        None, lambda: ingest_text(req.content, backend, source=req.source)
    )
    return {"ingested_chunks": len(ids)}


@app.get("/rag/status")
async def rag_status():
    from seraphim.memory import get_rag_backend
    backend = get_rag_backend()
    count = backend.count() if backend is not None else 0
    return {"enabled": backend is not None, "doc_count": count}


@app.delete("/rag/reset")
async def rag_reset():
    from seraphim.memory import get_rag_backend
    backend = get_rag_backend()
    if backend is None:
        raise HTTPException(status_code=503, detail="RAG backend not initialized")
    backend.clear()
    return {"ok": True}


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
    loop = asyncio.get_running_loop()
    audio_bytes = await loop.run_in_executor(
        None, partial(synthesize_to_bytes, req.text)
    )
    return StreamingResponse(
        iter([audio_bytes]),
        media_type="audio/wav",
        headers={"Content-Disposition": "inline; filename=response.wav"},
    )


# ─── STT / Whisper ───────────────────────────────────────────────────────────

_whisper_transcriber = None
_whisper_lock = asyncio.Lock()


async def _get_whisper():
    """Lazy-load the Whisper transcriber (singleton)."""
    global _whisper_transcriber
    if _whisper_transcriber is None:
        async with _whisper_lock:
            if _whisper_transcriber is None:
                from seraphim.voice.transcriber import Transcriber
                loop = asyncio.get_running_loop()
                _whisper_transcriber = await loop.run_in_executor(
                    None, lambda: Transcriber(model_size="base", language=None, device="cpu")
                )
    return _whisper_transcriber


@app.get("/stt/status")
async def stt_status():
    """Vérifie si faster-whisper est disponible."""
    try:
        from faster_whisper import WhisperModel  # noqa: F401
        return {"available": True, "model": "base"}
    except ImportError:
        return {"available": False, "model": None}


@app.post("/stt/transcribe")
async def stt_transcribe(audio: UploadFile = File(...)):
    """Transcrit un fichier audio avec faster-whisper (local, offline).

    Accepte audio/webm, audio/wav, audio/mp3, etc.
    Retourne {"text": "..."}.
    """
    try:
        t = await _get_whisper()
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="faster-whisper non installé. Lance : uv pip install 'seraphim[voice]'",
        )

    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="audio vide")

    suffix = Path(audio.filename or "audio.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(None, lambda: t.transcribe_file(tmp_path))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return {"text": text.strip()}
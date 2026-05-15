# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for seraphim-server (desktop bundle — no heavy ML deps)
# Build: pyinstaller seraphim.spec

import sys
from pathlib import Path

ROOT = Path(SPECPATH)

a = Analysis(
    [str(ROOT / "src" / "seraphim" / "cli.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[
        (str(ROOT / "configs"), "configs"),
    ],
    hiddenimports=[
        # uvicorn internals not auto-detected
        "uvicorn.lifespan.on",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.wsproto_impl",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        # fastapi / starlette
        "fastapi",
        "fastapi.middleware.cors",
        "starlette.middleware.cors",
        # pydantic
        "pydantic",
        "pydantic_settings",
        # aiosqlite
        "aiosqlite",
        # httpx
        "httpx",
        # yaml
        "yaml",
        # seraphim — force-collect all subpackages
        "seraphim",
        "seraphim.api",
        "seraphim.api.app",
        "seraphim.agents",
        "seraphim.agents.base",
        "seraphim.agents.core",
        "seraphim.agents.router",
        "seraphim.agents.learned_router",
        "seraphim.engine",
        "seraphim.engine.base",
        "seraphim.engine.ollama",
        "seraphim.engine.metrics",
        "seraphim.engine.vllm",
        "seraphim.engine.llamacpp",
        "seraphim.memory",
        "seraphim.memory.store",
        "seraphim.memory.context",
        "seraphim.memory.sqlite_fts",
        "seraphim.memory.embeddings",
        "seraphim.memory.chunking",
        "seraphim.memory.ingest",
        "seraphim.skills",
        "seraphim.skills.base",
        "seraphim.skills.registry",
        "seraphim.skills.manager",
        "seraphim.skills.loader",
        "seraphim.skills.executor",
        "seraphim.skills.core",
        "seraphim.skills.core.calculator",
        "seraphim.skills.core.code_interpreter",
        "seraphim.skills.core.shell",
        "seraphim.skills.core.http_request",
        "seraphim.skills.core.think",
        "seraphim.skills.web",
        "seraphim.skills.web.search",
        "seraphim.skills.system",
        "seraphim.skills.system.files",
        "seraphim.skills.system.control",
        "seraphim.skills.system.screen",
        "seraphim.skills.system.clipboard",
        "seraphim.skills.memory",
        "seraphim.skills.memory.sqlite",
        "seraphim.learning",
        "seraphim.learning.trace_store",
        "seraphim.learning.collector",
        "seraphim.learning.daemon",
        "seraphim.monitor",
        "seraphim.connectors",
        "seraphim.digest",
        "seraphim.voice",
        "seraphim.voice.speaker",
        "seraphim.voice.listener",
        "seraphim.voice.transcriber",
        "seraphim.settings",
        "seraphim.cli",
    ],
    excludes=[
        # Heavy ML/voice deps — excluded for slim bundle
        "torch",
        "torchaudio",
        "torchvision",
        "faster_whisper",
        "whisper",
        "chromadb",
        "sentence_transformers",
        "faiss",
        "sklearn",
        "scipy",
        "onnxruntime",
        "kokoro_onnx",
        "piper_tts",
        "sounddevice",
        "soundfile",
        "miniaudio",
        "pydub",
        "selenium",
        "dspy",
        "transformers",
        "huggingface_hub",
        # Test/dev
        "pytest",
        "mypy",
        "ruff",
        # Jupyter
        "notebook",
        "ipython",
        "ipykernel",
        # Other large optional
        "matplotlib",
        "pandas",
        "PIL",
        "cv2",
        "tensorflow",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="seraphim-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,        # no console window in production
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=str(ROOT / "seraphim-ui" / "src-tauri" / "icons" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="seraphim-server",
)

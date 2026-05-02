```powershell
quick start :
$env:PATH += ";C:\Users\ostap\AppData\Local\Programs\Ollama"
$env:Path = "C:\Users\ostap\.local\bin;$env:Path"
$env:PATH += ";C:\Users\ostap\.cargo\bin"
$env:PATH += ";C:\Program Files\nodejs"
$env:PATH += ";C:\Users\ostap\.cargo\bin"

terminal 1 :  uv run seraphim serve
terminal 2 :  npm run tauri dev
```

# Seraphim 🌟

*Your personal AI, running entirely on your machine.*

[
[
[
[
[

***

> **Seraphim** is a local-first personal AI assistant.
> It runs entirely on your machine via Ollama — no cloud, no data leaving your device, no subscriptions.
> It ships with a native desktop app (Tauri + React) and a full Python backend (FastAPI + CLI).

***

## Architecture

```
Seraphim
├── src/seraphim/          # Python backend
│   ├── agents/            # Agent framework (chat, coder, researcher…)
│   ├── api/               # FastAPI REST server
│   ├── engine/            # Multi-engine support (Ollama + others)
│   ├── memory/            # RAG system with pluggable backends
│   ├── skills/            # Skills loader & auto-routing
│   ├── voice/             # STT (Whisper) + TTS (Kokoro, Edge-TTS, Piper)
│   └── cli.py             # Typer CLI
├── seraphim-ui/           # Desktop app
│   ├── src/               # React + TypeScript frontend
│   └── src-tauri/         # Rust (Tauri) shell
└── configs/seraphim/      # YAML configuration files
```

***

## Features

- **Local by default** — everything runs on your hardware via Ollama, no API key needed
- **Multi-engine** — supports Ollama and other backends, switchable via config
- **Agent framework** — modular agents with automatic skill routing
- **Skills system** — extend agents with capabilities from OpenClaw or custom skills
- **Long-term memory (RAG)** — ChromaDB + FAISS + BM25 pluggable backends, context injection
- **Voice I/O** — Speech-to-text via Whisper (`faster-whisper`), TTS via Kokoro ONNX / Edge-TTS / Piper
- **Native desktop app** — Tauri + React UI with a Three.js WebGL animated orb
- **Web API** — FastAPI server at `http://localhost:7272`, usable from any client
- **IDE-first code agent** — dedicated coding workflow with file I/O and shell execution
- **Web search** — DuckDuckGo integration via `ddgs`

***

## Quick Start (Windows)

### Prerequisites

| Tool | Install |
|------|---------|
| Python 3.10–3.13 | [python.org](https://python.org) |
| [uv](https://astral.sh/uv) | see below |
| [Ollama](https://ollama.com) | `winget install Ollama.Ollama` |
| Node.js | [nodejs.org](https://nodejs.org) |
| Rust + Cargo | `winget install Rustlang.Rustup` |
| Visual C++ Build Tools | [visualstudio.microsoft.com](https://visualstudio.microsoft.com/fr/visual-cpp-build-tools/) — cocher **"Développement Desktop en C++"** |
| Git | [git-scm.com](https://git-scm.com) |

### First-time setup

```powershell
# 1. Install uv
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. Clone & install Python dependencies
git clone https://github.com/ostacheGIT/Seraphim.git
cd Seraphim
uv sync

# 3. Install frontend dependencies
npm install

# 4. Pull a local model
ollama pull llama3.2:1b
```

> **Tip — persist your PATH** to avoid re-running these on every terminal session.
> Run once, then restart your terminal:
> ```powershell
> Add-Content -Path $PROFILE -Value @"
> `$env:PATH += ";C:\Users\$env:USERNAME\AppData\Local\Programs\Ollama"
> `$env:Path  = "C:\Users\$env:USERNAME\.local\bin;`$env:Path"
> `$env:PATH += ";C:\Users\$env:USERNAME\.cargo\bin"
> `$env:PATH += ";C:\Program Files\nodejs"
> "@
> ```

***

### Daily launch (2 terminals)

**Terminal 1 — Python backend**
```powershell
uv run seraphim serve
# API available at http://localhost:7272
```

**Terminal 2 — Desktop app**
```powershell
npm run tauri dev
```

***

## CLI

```bash
# Ask a question (default chat agent)
uv run seraphim ask "What can you do?"

# Use a specific agent
uv run seraphim ask "Refactor this function" --agent coder
uv run seraphim ask "Summarize my notes"     --agent memory

# Start the API server only
uv run seraphim serve

# Initialize / reset config
uv run seraphim init
```

***

## Agents

| Agent | Description |
|-------|-------------|
| `chat` | Conversational agent (default) |
| `coder` | IDE-first code generation with file I/O and shell execution |
| `researcher` | Web search (DuckDuckGo) + local document research with citations |
| `memory` | Long-term memory retrieval and knowledge management |

***

## Skills

Skills extend agents with new capabilities and are auto-routed — Seraphim picks the right skill automatically based on your query.

```bash
# Install a skill from OpenClaw
seraphim skill install arxiv
seraphim skill install file-summarizer

# List installed skills
seraphim skill list

# Create your own skill
seraphim skill new my-custom-skill
```

Skills follow the [agentskills.io](https://agentskills.io/specification) standard (`SKILL.md` + optional scripts).
Browse available skills at [github.com/openclaw/openclaw/tree/main/skills](https://github.com/openclaw/openclaw/tree/main/skills).

***

## Memory (RAG)

Seraphim includes a pluggable RAG system with three backends:

| Backend | Use case |
|---------|----------|
| **ChromaDB** | Default vector store, persistent across sessions |
| **FAISS** | Fast in-memory similarity search |
| **BM25** | Keyword-based retrieval (rank-bm25) |

Install the memory extras for full support:
```bash
uv sync --extra memory
```

***

## Voice

| Direction | Engine | Notes |
|-----------|--------|-------|
| **STT** (Speech → Text) | `faster-whisper` | Local Whisper model, no cloud |
| **TTS** (Text → Speech) | `kokoro-onnx` | Primary, high quality |
| **TTS** | `edge-tts` | Fallback, Microsoft neural voices |
| **TTS** | `piper-tts` | Fully offline alternative |

Custom voice profiles are stored in `voices/`.

***

## Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.10+, FastAPI, Uvicorn |
| CLI | Typer + Rich |
| LLM engine | Ollama (multi-engine ready) |
| Memory | ChromaDB, FAISS, BM25 |
| Voice STT | faster-whisper (Torch 2.6) |
| Voice TTS | Kokoro ONNX, Edge-TTS, Piper |
| Desktop shell | Tauri (Rust) |
| Frontend | React + TypeScript + Vite |
| 3D UI | Three.js (WebGL particle sphere) |
| Web search | DuckDuckGo (`ddgs`) |
| Config | YAML + pydantic-settings |

***

## 🗺️ Roadmap

- [x] Core engine (Ollama integration, multi-engine support)
- [x] CLI (`seraphim ask`, `seraphim init`, `seraphim serve`)
- [x] Agent framework with auto-routing
- [x] Skills system (OpenClaw compatible)
- [x] Long-term memory (RAG — ChromaDB + FAISS + BM25)
- [x] Voice I/O (Whisper STT + Kokoro/Edge-TTS/Piper TTS)
- [x] Native desktop app (Tauri + React + Three.js)
- [x] IDE-first code generation workflow
- [ ] Multi-agent orchestration
- [ ] Plugin marketplace
- [ ] Mobile companion app

***

## 📄 License

[Apache 2.0](LICENSE) — fork it, modify it, ship it.
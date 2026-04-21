# Seraphim 🌟

*Your personal AI, running entirely on your machine.*

[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue)](https://python.org)
[![License](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)
[![Powered by Ollama](https://img.shields.io/badge/engine-Ollama-orange)](https://ollama.com)
[![Status](https://img.shields.io/badge/status-alpha-red)]()

---

> **Seraphim** is a local-first personal AI assistant built on top of the [OpenJarvis](https://github.com/open-jarvis/OpenJarvis) framework.  
> It runs entirely on your machine via Ollama — no cloud, no data leaving your device, no subscriptions.

---

## Philosophy

- **Local by default** — everything runs on your hardware via Ollama
- **Simple to install** — one command to get started
- **Agent-first** — modular agents for automation, research, coding, and more
- **Extensible** — build and share your own skills and agents
- **Beautiful** — a clean web UI to interact naturally with your AI

---

## Quick Start

### Prerequisites

| Tool | Install |
|------|---------|
| Python 3.10+ | [python.org](https://python.org) |
| [uv](https://astral.sh/uv) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| [Ollama](https://ollama.com) | [ollama.com](https://ollama.com) |
| Rust | curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh |
| Git | [git-scm.com](https://git-scm.com) |

backend terminal 1 :
winget install Ollama.Ollama
$env:PATH += ";C:\Users\ostap\AppData\Local\Programs\Ollama"
ollama pull llama3.2:1b
uv run seraphim serve

powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
$env:Path = "C:\Users\ostap\.local\bin;$env:Path"                                    
uv sync

frontend terminal 2 : 

telecharger les visual C++ tools : https://visualstudio.microsoft.com/fr/visual-cpp-build-tools/
coche obligatoirement : "Développement Desktop en C++" (C++ build tools) Clique Installer.
$env:PATH += ";C:\Users\ostap\.cargo\bin"

https://nodejs.org             (rouvrir le PS ou bien ajouter le path manuellement :  $env:PATH += ";C:\Program Files\nodejs" )
node --version

winget install Rustlang.Rustup
$env:PATH += ";C:\Users\ostap\.cargo\bin"

npm install
npm run tauri dev



quick start : 
terminal 1 :  uv run seraphim serve
terminal 2 :  npm run tauri dev


### Setup

```bash
# 1. Clone & install
git clone https://github.com/YOUR_USERNAME/Seraphim.git
cd Seraphim
uv sync

# 2. Pull a local model
ollama pull llama3.2

# 3. Initialize Seraphim
uv run seraphim init

# 4. Ask something
uv run seraphim ask "What can you do?"

# 5. (Optional) Start the web UI
uv run seraphim serve
```

Open your browser at `http://localhost:7272` and start chatting 🎉

---

## Built-in Agents

| Agent | Description |
|-------|-------------|
| `chat` | Simple conversational agent (default) |
| `researcher` | Web + local document research with citations |
| `coder` | Code assistant with file I/O and shell execution |
| `automator` | Task automation agent with scheduling |
| `memory` | Long-term memory and knowledge retrieval |

```bash
# Use a specific agent
uv run seraphim ask "Refactor this function" --agent coder
uv run seraphim ask "Summarize my notes" --agent memory
```

---

## Skills

Skills extend agents with new capabilities. Install from community sources or write your own:

```bash
# Install a skill
seraphim skill install arxiv
seraphim skill install file-summarizer

# List installed skills
seraphim skill list

# Create your own
seraphim skill new my-custom-skill
```

---

## 🗺️ Roadmap

- [x] Core engine (Ollama integration)
- [x] CLI (`seraphim ask`, `seraphim init`, `seraphim serve`)
- [x] Basic agent framework
- [x] Skill system
- [ ] Web UI (React)
- [ ] Long-term memory (RAG)
- [ ] Voice input/output
- [ ] Plugin marketplace
- [ ] Multi-agent orchestration

---

## 📄 License

[Apache 2.0](LICENSE) — fork it, modify it, ship it.

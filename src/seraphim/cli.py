"""Seraphim CLI — entry point for all terminal commands."""

import os
os.environ["PYTHONUTF8"] = "1"

import asyncio
import uuid
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from seraphim import version

app = typer.Typer(
    name="seraphim",
    help="Seraphim — Your personal AI, running entirely on your machine.",
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()


def _run(coro):
    return asyncio.run(coro)


@app.command()
def init():
    """Initialize Seraphim and detect your local setup."""
    console.print(Panel.fit(
        f"[bold cyan]🌟 Seraphim v{version}[/bold cyan]\n[dim]Personal AI, running entirely on your machine.[/dim]",
        border_style="cyan",
    ))

    async def _init():
        from seraphim.engine.ollama import engine
        from seraphim.memory.store import init_db

        console.print("\n[bold]Checking Ollama...[/bold]")
        ok = await engine.health_check()
        if ok:
            console.print("  [green]✓[/green] Ollama is running")
            models = await engine.list_models()
            if models:
                console.print(f"  [green]✓[/green] Available models: {', '.join(models)}")
            else:
                console.print("  [yellow]⚠[/yellow] No models found. Run [bold]ollama pull llama3.2[/bold]")
        else:
            console.print("  [red]✗[/red] Ollama not found.")
            raise typer.Exit(1)

        console.print("\n[bold]Creating directories...[/bold]")
        from pathlib import Path
        home = Path.home() / ".seraphim"
        for d in [home, home / "skills", home / "memory"]:
            d.mkdir(parents=True, exist_ok=True)
        console.print(f"  [green]✓[/green] Config dir: {home}")

        await init_db()
        console.print("  [green]✓[/green] Memory database initialized")

        console.print("\n[bold green]✓ Seraphim is ready![/bold green]")
        console.print('Run [bold]seraphim ask "Hello!"[/bold] to get started.')

    _run(_init())


@app.command()
def ask(
        query: str = typer.Argument(..., help="Your question or instruction"),
        agent: str = typer.Option("chat", "--agent", "-a", help="Agent: chat, coder, researcher, react"),
        model: Optional[str] = typer.Option(None, "--model", "-m", help="Override the default model (compat)"),
        engine: Optional[str] = typer.Option(
            None,
            "--engine",
            "-e",
            help="Engine ID (e.g. ollama_qwen3b, ollama_qwen7b). Overrides --model.",
        ),
        stream: bool = typer.Option(True, "--stream/--no-stream", help="Stream the response"),
        session: Optional[str] = typer.Option(None, "--session", "-s", help="Session ID for memory"),
        no_memory: bool = typer.Option(False, "--no-memory", help="Disable memory for this query"),
):
    """Ask Seraphim a question."""

    async def _ask():
        from seraphim.agents.base import get_agent
        from seraphim.agents.core import AgentContext
        from seraphim.agents.react import ReactAgent
        from seraphim.engine import get_engine
        from seraphim.memory.store import init_db, load_history, save_message

        sess = session or str(uuid.uuid4())[:8]

        # Choix de l'engine_id:
        engine_id: Optional[str] = engine
        if engine_id is None and model:
            if "7b" in model:
                engine_id = "ollama_qwen7b"
            else:
                engine_id = "ollama_qwen3b"

        # Vérifie que l'engine existe si spécifié
        if engine_id is not None:
            _ = get_engine(engine_id)

        # Instancie l'agent
        if agent == "react":
            ag = ReactAgent(engine_id=engine_id)
        else:
            ag = get_agent(agent)
            if hasattr(ag, "engine_id"):
                ag.engine_id = engine_id  # type: ignore[assignment]

        ctx = AgentContext()
        ctx.add_system(ag.system_prompt)

        if not no_memory:
            await init_db()
            history = await load_history(sess)
            for msg in history:
                ctx.messages.append(msg)

        ctx.add_user(query)

        if stream:
            console.print(
                f"[dim]Seraphim ({agent}) [{sess}] engine={engine_id or 'default'} ›[/dim] ",
                end="",
            )
            full_response = await ag.run(query, ctx)
            console.print(full_response)
        else:
            with console.status("[dim]Thinking...[/dim]"):
                full_response = await ag.run(query, ctx)
            console.print(
                f"[dim]Seraphim ({agent}) [{sess}] engine={engine_id or 'default'} ›[/dim]"
            )
            console.print(Markdown(full_response))

        if not no_memory:
            await save_message(sess, "user", query, agent)
            await save_message(sess, "assistant", full_response, agent)

    _run(_ask())


@app.command()
def history(
        session: Optional[str] = typer.Option(None, "--session", "-s", help="Session ID to display"),
        list_all: bool = typer.Option(False, "--list", "-l", help="List all sessions"),
        delete: Optional[str] = typer.Option(None, "--delete", "-d", help="Delete a session"),
):
    """Browse or manage conversation history."""

    async def _history():
        from seraphim.memory.store import init_db, load_history, list_sessions, delete_session

        await init_db()

        if delete:
            await delete_session(delete)
            console.print(f"[green]✓[/green] Session [bold]{delete}[/bold] deleted.")
            return

        if list_all or not session:
            sessions = await list_sessions()
            if not sessions:
                console.print("[dim]No conversations yet.[/dim]")
                return
            console.print("[bold]Sessions:[/bold]\n")
            for s in sessions:
                console.print(f"  [cyan]{s['session']}[/cyan] [{s['agent']}] {s['timestamp'][:16]}")
                console.print(f"    [dim]{s['preview']}...[/dim]\n")
            return

        messages = await load_history(session, limit=50)
        if not messages:
            console.print(f"[yellow]No messages found for session '{session}'.[/yellow]")
            return

        console.print(f"[bold]Session:[/bold] {session}\n")
        for msg in messages:
            if msg["role"] == "user":
                console.print(f"[bold cyan]You ›[/bold cyan] {msg['content']}")
            else:
                console.print("[bold green]Seraphim ›[/bold green]")
                console.print(Markdown(msg["content"]))
            console.print()

    _run(_history())


@app.command()
def serve(
        host: str = typer.Option("0.0.0.0", help="Host to bind"),
        port: int = typer.Option(7272, help="Port to listen on"),
        reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes"),
):
    """Start the Seraphim web server."""
    console.print(f"[bold cyan]Starting Seraphim server[/bold cyan] at http://{host}:{port}")
    import uvicorn
    from seraphim.api.app import app as api_app
    uvicorn.run(api_app, host=host, port=port, reload=reload)


@app.command()
def models():
    """List available local Ollama models."""

    async def _models():
        from seraphim.engine.ollama import engine
        ok = await engine.health_check()
        if not ok:
            console.print("[red]✗ Ollama is not running.[/red]")
            raise typer.Exit(1)
        model_list = await engine.list_models()
        if not model_list:
            console.print("[yellow]No models found. Run ollama pull llama3.2[/yellow]")
        else:
            console.print("[bold]Available models[/bold]")
            for m in model_list:
                marker = "[green]●[/green]" if m == engine.model else " "
                console.print(f"  {marker} {m}")

    _run(_models())


@app.command()
def doctor():
    """Diagnose your Seraphim setup."""

    async def _doctor():
        from seraphim.engine.ollama import engine
        from seraphim.settings import settings

        console.print("[bold]Seraphim Doctor[/bold]\n")

        ok = await engine.health_check()
        status = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"  {status} Ollama reachable at {settings.engine.base_url}")

        if ok:
            models = await engine.list_models()
            has_model = any(settings.engine.model in m for m in models)
            status = "[green]✓[/green]" if has_model else "[yellow]⚠[/yellow]"
            console.print(f"  {status} Default model '{settings.engine.model}'")
            if not has_model:
                console.print(f"     → Run: [bold]ollama pull {settings.engine.model}[/bold]")

        from pathlib import Path
        home = Path.home() / ".seraphim"
        status = "[green]✓[/green]" if home.exists() else "[yellow]⚠[/yellow]"
        console.print(f"  {status} Seraphim home: {home}")

        db = home / "memory.db"
        status = "[green]✓[/green]" if db.exists() else "[yellow]⚠[/yellow]"
        console.print(f"  {status} Memory DB: {db}")

        console.print(f"\n[dim]Version:[/dim] {version}")

    _run(_doctor())


@app.command("version")
def show_version():
    """Show Seraphim version."""
    console.print(f"Seraphim v{version}")


# ── Commande vocale ──────────────────────────────────────────────────────────
from seraphim.voice.cli_voice import listen_command
app.command("listen")(listen_command)


if __name__ == "__main__":
    app()
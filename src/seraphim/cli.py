"""
Seraphim CLI — entry point for all terminal commands.

Usage:
    seraphim init
    seraphim ask "Your question here"
    seraphim ask "Fix this code" --agent coder
    seraphim serve
    seraphim models
    seraphim doctor
"""

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.live import Live

from seraphim import __version__

app = typer.Typer(
    name="seraphim",
    help="🌟 Seraphim — Your personal AI, running entirely on your machine.",
    add_completion=False,
    rich_markup_mode="rich",
)

console = Console()


def _run(coro):
    return asyncio.run(coro)


# ─── Commands ────────────────────────────────────────────────────────────────


@app.command()
def init():
    """Initialize Seraphim and detect your local setup."""
    console.print(Panel.fit(
        f"[bold cyan]🌟 Seraphim v{__version__}[/bold cyan]\n"
        "[dim]Personal AI, running entirely on your machine.[/dim]",
        border_style="cyan",
    ))

    async def _init():
        from seraphim.engine.ollama import engine
        console.print("\n[bold]Checking Ollama...[/bold]")
        ok = await engine.health_check()
        if ok:
            console.print("  [green]✓[/green] Ollama is running")
            models = await engine.list_models()
            if models:
                console.print(f"  [green]✓[/green] Available models: {', '.join(models)}")
            else:
                console.print(
                    "  [yellow]⚠[/yellow] No models found. Run: [bold]ollama pull llama3.2[/bold]"
                )
        else:
            console.print(
                "  [red]✗[/red] Ollama not found. Install it at [link]https://ollama.com[/link]"
            )
            raise typer.Exit(1)

        console.print("\n[bold]Creating directories...[/bold]")
        import os
        from pathlib import Path
        home = Path.home() / ".seraphim"
        for d in [home, home / "skills", home / "memory"]:
            d.mkdir(parents=True, exist_ok=True)
        console.print(f"  [green]✓[/green] Config dir: {home}")

        console.print("\n[bold green]✓ Seraphim is ready![/bold green]")
        console.print("Run [bold]seraphim ask \"Hello!\"[/bold] to get started.")

    _run(_init())


@app.command()
def ask(
    query: str = typer.Argument(..., help="Your question or instruction"),
    agent: str = typer.Option("chat", "--agent", "-a", help="Agent to use (chat, coder, researcher)"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override the default model"),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="Stream the response"),
):
    """Ask Seraphim a question."""

    async def _ask():
        from seraphim.agents.base import get_agent
        from seraphim.settings import settings

        if model:
            settings.engine.model = model

        ag = get_agent(agent)

        if stream:
            from seraphim.engine.ollama import engine as eng
            from seraphim.agents.base import AgentContext
            ctx = AgentContext()
            ctx.add_system(ag.system_prompt)
            ctx.add_user(query)

            console.print(f"\n[dim]Seraphim ({agent}) ›[/dim] ", end="")
            full_response = ""
            async for token in eng.stream_chat(ctx.messages):
                console.print(token, end="", highlight=False)
                full_response += token
            console.print()
        else:
            with console.status("[dim]Thinking...[/dim]"):
                response = await ag.run(query)
            console.print(f"\n[dim]Seraphim ({agent}) ›[/dim]")
            console.print(Markdown(response))

    _run(_ask())


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Host to bind"),
    port: int = typer.Option(7272, help="Port to listen on"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes"),
):
    """Start the Seraphim web server."""
    console.print(f"[bold cyan]🌟 Starting Seraphim server[/bold cyan] at http://{host}:{port}")
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
            console.print("[yellow]No models found. Run: ollama pull llama3.2[/yellow]")
        else:
            console.print("[bold]Available models:[/bold]")
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

        # Engine
        ok = await engine.health_check()
        status = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"  {status} Ollama reachable at {settings.engine.base_url}")

        # Model
        if ok:
            models = await engine.list_models()
            has_model = any(settings.engine.model in m for m in models)
            status = "[green]✓[/green]" if has_model else "[yellow]⚠[/yellow]"
            console.print(f"  {status} Default model '{settings.engine.model}'")
            if not has_model:
                console.print(
                    f"     → Run: [bold]ollama pull {settings.engine.model}[/bold]"
                )

        # Directories
        from pathlib import Path
        home = Path.home() / ".seraphim"
        status = "[green]✓[/green]" if home.exists() else "[yellow]⚠[/yellow]"
        console.print(f"  {status} Seraphim home: {home}")

        console.print("\n[dim]Version:[/dim]", __version__)

    _run(_doctor())


@app.command()
def version():
    """Show Seraphim version."""
    console.print(f"Seraphim v{__version__}")


if __name__ == "__main__":
    app()

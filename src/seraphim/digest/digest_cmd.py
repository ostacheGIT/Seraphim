"""CLI commands for morning_digest."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule

app = typer.Typer(
    name="digest",
    help="morning_digest — daily briefing: weather, news, monitors.",
    add_completion=False,
)
console = Console()


def _render_digest(digest) -> None:
    console.print(Rule(f"[bold cyan]Morning Digest — {digest.date}[/bold cyan]", style="cyan"))
    console.print()

    for section in digest.sections:
        if section.error:
            console.print(Panel(
                f"[red]{section.error}[/red]",
                title=f"[dim]{section.title}[/dim]",
                border_style="dim red",
            ))
        else:
            console.print(Panel(
                Markdown(section.content) if section.content else "[dim]No data[/dim]",
                title=f"[bold]{section.title}[/bold]",
                border_style="cyan",
            ))
        console.print()

    if digest.summary:
        console.print(Panel(
            digest.summary,
            title="[bold magenta]Seraphim's Take[/bold magenta]",
            border_style="magenta",
        ))
        console.print()


@app.command("run")
def run_digest(
    save: bool = typer.Option(False, "--save", "-s", help="Save digest to ~/.seraphim/digests/"),
    city: Optional[str] = typer.Option(None, "--city", "-c", help="Override city for weather"),
    no_summary: bool = typer.Option(False, "--no-summary", help="Skip LLM summary"),
):
    """Run morning digest now and display it."""

    async def _run():
        from seraphim.digest.builder import build_digest, load_config, save_config

        cfg = load_config()
        if city:
            cfg["city"] = city

        if no_summary:
            cfg["_skip_summary"] = True

        console.print("[dim]Fetching digest...[/dim]")
        digest = await build_digest(cfg)
        _render_digest(digest)

        if save:
            save_dir = Path(cfg["save_dir"])
            save_dir.mkdir(parents=True, exist_ok=True)
            filename = save_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md"
            filename.write_text(digest.to_markdown(), "utf-8")
            console.print(f"[green]✓[/green] Saved to {filename}")

    asyncio.run(_run())


@app.command("config")
def config(
    city: Optional[str] = typer.Option(None, "--city", "-c", help="City for weather"),
    topics: Optional[str] = typer.Option(None, "--topics", "-t", help="Comma-separated topics: tech,AI,crypto"),
    language: Optional[str] = typer.Option(None, "--language", "-l", help="Summary language: fr or en"),
    show: bool = typer.Option(False, "--show", help="Show current config"),
):
    """Configure morning digest settings."""
    from seraphim.digest.builder import load_config, save_config

    cfg = load_config()

    if show or (city is None and topics is None and language is None):
        console.print("[bold]Current digest config:[/bold]")
        console.print(f"  city:     [cyan]{cfg['city']}[/cyan]")
        console.print(f"  topics:   [cyan]{', '.join(cfg['topics'])}[/cyan]")
        console.print(f"  language: [cyan]{cfg.get('language', 'fr')}[/cyan]")
        console.print(f"  save_dir: [dim]{cfg['save_dir']}[/dim]")
        return

    if city:
        cfg["city"] = city
        console.print(f"[green]✓[/green] City set to [bold]{city}[/bold]")
    if topics:
        cfg["topics"] = [t.strip() for t in topics.split(",") if t.strip()]
        console.print(f"[green]✓[/green] Topics: {cfg['topics']}")
    if language:
        if language not in ("fr", "en"):
            console.print("[red]Language must be 'fr' or 'en'.[/red]")
            raise typer.Exit(1)
        cfg["language"] = language
        console.print(f"[green]✓[/green] Language: {language}")

    save_config(cfg)


@app.command("schedule")
def schedule(
    time_str: str = typer.Option("07:30", "--time", "-t", help="Daily time HH:MM"),
    remove: bool = typer.Option(False, "--remove", help="Remove scheduled task"),
):
    """Schedule morning digest via Windows Task Scheduler."""
    if sys.platform != "win32":
        console.print("[red]Windows only. Use cron on Linux/macOS.[/red]")
        raise typer.Exit(1)

    task_name = "SeraphimMorningDigest"

    if remove:
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", task_name, "/F"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            console.print(f"[green]✓[/green] Scheduled task '{task_name}' removed.")
        else:
            console.print(f"[red]✗[/red] {result.stderr.strip()}")
        return

    # Validate time format
    try:
        hour, minute = time_str.split(":")
        int(hour), int(minute)
    except ValueError:
        console.print("[red]Invalid time format. Use HH:MM (e.g. 07:30)[/red]")
        raise typer.Exit(1)

    python_exe = sys.executable
    cmd_args = f'"{python_exe}" -m seraphim.cli digest run --save'

    result = subprocess.run(
        [
            "schtasks", "/Create", "/F",
            "/TN", task_name,
            "/TR", cmd_args,
            "/SC", "DAILY",
            "/ST", time_str,
        ],
        capture_output=True, text=True,
    )

    if result.returncode == 0:
        console.print(f"[green]✓[/green] Scheduled '{task_name}' daily at {time_str}.")
        console.print(f"[dim]Command: {cmd_args}[/dim]")
    else:
        console.print(f"[red]✗[/red] {result.stderr.strip() or result.stdout.strip()}")

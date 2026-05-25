"""CLI commands for workflow orchestration: `seraphim workflow`."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="workflow", help="Multi-agent workflow orchestration — run, list, show, validate.")
console = Console()


@app.command("list")
def list_workflows():
    """List installed workflows from ~/.seraphim/workflows/."""
    from seraphim.workflow.loader import WorkflowLoader
    names = WorkflowLoader().list_all()
    if not names:
        root = Path("~/.seraphim/workflows").expanduser()
        console.print(f"[dim]No workflows found at {root}[/dim]")
        return
    for name in names:
        console.print(f"  [cyan]{name}[/cyan]")


@app.command("show")
def show_workflow(name: str = typer.Argument(..., help="Workflow name")):
    """Print nodes and edges of a workflow."""
    from seraphim.workflow.loader import WorkflowLoader
    try:
        graph = WorkflowLoader().load(name)
    except FileNotFoundError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)

    console.print(f"\n[bold]Workflow:[/bold] {graph.name}\n")
    t = Table(show_header=True)
    t.add_column("ID")
    t.add_column("Type")
    t.add_column("Config")
    for nid, node in graph.nodes.items():
        t.add_row(nid, node.type.value, json.dumps(node.config))
    console.print(t)

    if graph.edges:
        console.print("\n[bold]Edges:[/bold]")
        for edge in graph.edges:
            cond = f"  [dim]({edge.condition})[/dim]" if edge.condition else ""
            console.print(f"  {edge.src} → {edge.dst}{cond}")
    console.print()


@app.command("validate")
def validate_workflow(name: str = typer.Argument(..., help="Workflow name")):
    """Check a workflow for cycles and missing node references."""
    from seraphim.workflow.loader import WorkflowLoader
    try:
        graph = WorkflowLoader().load(name)
    except FileNotFoundError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)

    errors = graph.validate()
    if errors:
        for err in errors:
            console.print(f"[red]✗[/red] {err}")
        raise typer.Exit(1)
    console.print(f"[green]✓[/green] Workflow '{name}' is valid ({len(graph.nodes)} nodes).")


@app.command("run")
def run_workflow(
    name: str = typer.Argument(..., help="Workflow name"),
    input_text: str = typer.Option("", "--input", "-i", help="Initial input text"),
    json_inputs: str = typer.Option("", "--inputs", "-I", help="JSON dict of inputs"),
):
    """Run a workflow and print all node outputs."""
    from seraphim.workflow.loader import WorkflowLoader
    from seraphim.workflow.engine import WorkflowEngine
    from seraphim.settings import settings

    try:
        graph = WorkflowLoader().load(name)
    except FileNotFoundError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)

    errors = graph.validate()
    if errors:
        for err in errors:
            console.print(f"[red]✗[/red] {err}")
        raise typer.Exit(1)

    inputs: dict = {}
    if json_inputs:
        try:
            inputs = json.loads(json_inputs)
        except json.JSONDecodeError as e:
            console.print(f"[red]✗[/red] Invalid JSON: {e}")
            raise typer.Exit(1)
    if input_text:
        inputs.setdefault("input", input_text)

    async def _run():
        engine = WorkflowEngine(
            max_parallel=settings.workflow.max_parallel,
            timeout_secs=settings.workflow.timeout_secs,
        )
        with console.status(f"[dim]Running '{name}'...[/dim]"):
            ctx = await engine.run(graph, inputs)

        console.print("\n[bold]Results:[/bold]")
        for nid, output in ctx.outputs.items():
            console.print(f"  [cyan]{nid}:[/cyan] {output}")

    asyncio.run(_run())

"""CLI commands for monitor_operative."""

from __future__ import annotations

import asyncio
import re
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="monitor",
    help="monitor_operative — continuous background monitoring.",
    add_completion=False,
)
console = Console()

_INTERVAL_RE = re.compile(r"^(\d+)(s|m|h)$")


def _parse_interval(value: str) -> int:
    """Parse '5m', '30s', '1h' → seconds. Bare int assumed seconds."""
    m = _INTERVAL_RE.match(value.strip())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return n * {"s": 1, "m": 60, "h": 3600}[unit]
    if value.isdigit():
        return int(value)
    raise typer.BadParameter(f"Invalid interval '{value}'. Use: 30s, 5m, 1h")


@app.command("add")
def add(
    name: str = typer.Argument(..., help="Monitor name (unique ID)"),
    condition: str = typer.Argument(..., help="Natural-language condition to evaluate"),
    interval: str = typer.Option("5m", "--interval", "-i", help="Check interval: 30s, 5m, 1h"),
    action: str = typer.Option("notify", "--action", "-a", help="Action on trigger: notify"),
):
    """Add a new monitor.

    Examples:
      seraphim monitor add btc-alert "BTC price drops below 90000 USD" --interval 5m
      seraphim monitor add news-python "New Python release announced" --interval 1h
    """
    async def _add():
        from seraphim.monitor.store import init_db, add_monitor
        await init_db()
        secs = _parse_interval(interval)
        mid = await add_monitor(name, condition, secs, action)
        console.print(f"[green]✓[/green] Monitor [bold]{name}[/bold] added (id={mid}, every {secs}s).")

    asyncio.run(_add())


@app.command("list")
def list_cmd():
    """List all monitors."""
    async def _list():
        from seraphim.monitor.store import init_db, list_monitors
        import datetime
        await init_db()
        monitors = await list_monitors()
        if not monitors:
            console.print("[dim]No monitors. Add one with: seraphim monitor add[/dim]")
            return
        tbl = Table(show_header=True, header_style="bold cyan")
        tbl.add_column("Name", style="bold")
        tbl.add_column("Condition", max_width=40)
        tbl.add_column("Interval")
        tbl.add_column("Enabled")
        tbl.add_column("Last check")
        tbl.add_column("Triggered", justify="right")
        for m in monitors:
            last = (
                datetime.datetime.fromtimestamp(m["last_check"]).strftime("%H:%M:%S")
                if m["last_check"] else "—"
            )
            tbl.add_row(
                m["name"],
                m["condition"][:40],
                f"{m['interval_secs']}s",
                "[green]on[/green]" if m["enabled"] else "[dim]off[/dim]",
                last,
                str(m["triggered_count"]),
            )
        console.print(tbl)

    asyncio.run(_list())


@app.command("enable")
def enable(name: str = typer.Argument(...)):
    """Enable a monitor."""
    async def _en():
        from seraphim.monitor.store import init_db, set_enabled
        await init_db()
        await set_enabled(name, True)
        console.print(f"[green]✓[/green] Monitor [bold]{name}[/bold] enabled.")
    asyncio.run(_en())


@app.command("disable")
def disable(name: str = typer.Argument(...)):
    """Disable a monitor (keeps it in DB)."""
    async def _dis():
        from seraphim.monitor.store import init_db, set_enabled
        await init_db()
        await set_enabled(name, False)
        console.print(f"[yellow]–[/yellow] Monitor [bold]{name}[/bold] disabled.")
    asyncio.run(_dis())


@app.command("delete")
def delete(
    name: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Delete a monitor permanently."""
    if not yes:
        typer.confirm(f"Delete monitor '{name}'?", abort=True)

    async def _del():
        from seraphim.monitor.store import init_db, delete_monitor
        await init_db()
        await delete_monitor(name)
        console.print(f"[red]✗[/red] Monitor [bold]{name}[/bold] deleted.")
    asyncio.run(_del())


@app.command("run")
def run(
    name: str = typer.Argument(..., help="Monitor name to run once"),
):
    """Run a single monitor check right now (one-shot)."""
    async def _run():
        from seraphim.monitor.store import init_db, get_monitor, update_check
        from seraphim.monitor.daemon import _check_one
        await init_db()
        m = await get_monitor(name)
        if not m:
            console.print(f"[red]✗ Monitor '{name}' not found.[/red]")
            raise typer.Exit(1)
        console.print(f"[dim]Checking '{name}'...[/dim]")
        await _check_one(m)
        from seraphim.monitor.store import get_monitor as gm
        updated = await gm(name)
        if updated:
            console.print(f"[bold]Result:[/bold] {updated['last_result']}")
    asyncio.run(_run())


@app.command("start")
def start(
    once: bool = typer.Option(False, "--once", help="Single pass, then exit"),
):
    """Start the monitoring daemon (blocks). Ctrl+C to stop."""
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def _start():
        from seraphim.monitor.daemon import run_daemon
        await run_daemon(once=once)

    console.print("[bold cyan]monitor_operative starting...[/bold cyan]  (Ctrl+C to stop)")
    try:
        asyncio.run(_start())
    except KeyboardInterrupt:
        console.print("\n[dim]monitor_operative stopped.[/dim]")

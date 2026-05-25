"""CLI commands for the channel daemon: `seraphim channel`."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time

import typer
from rich.console import Console
from rich.table import Table

from seraphim.channels.daemon import PID_FILE, STOP_FILE, is_alive, read_state

app = typer.Typer(name="channel", help="Messaging channel daemon — Telegram, Slack, Webhook.")
console = Console()


@app.command("list")
def list_channels():
    """List all registered channel types."""
    import seraphim.channels.telegram  # noqa: F401
    import seraphim.channels.slack     # noqa: F401
    from seraphim.channels.base import ChannelRegistry
    names = ChannelRegistry.list_names()
    if not names:
        console.print("[dim]No channels registered.[/dim]")
        return
    from seraphim.settings import settings
    ch = settings.channels
    enabled = ChannelRegistry.get_enabled()
    t = Table(show_header=True)
    t.add_column("Channel")
    t.add_column("Enabled")
    for name in names:
        flag = "[green]yes[/green]" if name in enabled else "[dim]no[/dim]"
        t.add_row(name, flag)
    console.print(t)


@app.command("start")
def start_daemon():
    """Start the channel daemon in the background."""
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            if is_alive(pid):
                console.print(f"[yellow]⚠[/yellow] Daemon already running (pid={pid})")
                return
        except Exception:
            pass

    subprocess.Popen(
        [sys.executable, "-m", "seraphim.channels.daemon"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(1.5)
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            console.print(f"[green]✓[/green] Channel daemon started (pid={pid})")
        except Exception:
            console.print("[green]✓[/green] Channel daemon started")
    else:
        console.print("[red]✗[/red] Daemon failed to start — check ~/.seraphim/channels/channel_daemon.log")


@app.command("stop")
def stop_daemon():
    """Stop the channel daemon gracefully."""
    if not PID_FILE.exists():
        console.print("[dim]Daemon not running.[/dim]")
        return
    STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    STOP_FILE.touch()
    for _ in range(30):
        time.sleep(1)
        if not PID_FILE.exists():
            console.print("[green]✓[/green] Channel daemon stopped.")
            return
    console.print("[yellow]⚠[/yellow] Daemon did not stop within 30s — PID file still present.")


@app.command("status")
def daemon_status():
    """Show channel daemon and per-channel status."""
    state = read_state()
    if not state or state.get("status") == "stopped":
        running = False
        if PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text().strip())
                running = is_alive(pid)
            except Exception:
                pass
        label = "[green]running[/green]" if running else "[red]stopped[/red]"
        console.print(f"\nDaemon: {label}")
        if not state:
            console.print()
            return

    started_at = state.get("started_at", "")
    if started_at:
        console.print(f"  Started: {started_at}")

    channels = state.get("channels", {})
    if channels:
        t = Table(show_header=True)
        t.add_column("Channel")
        t.add_column("Status")
        t.add_column("Started at")
        t.add_column("Error")
        for name, info in channels.items():
            st = info.get("status", "?")
            color = "green" if st == "running" else "red"
            t.add_row(
                name,
                f"[{color}]{st}[/{color}]",
                info.get("started_at", ""),
                info.get("error", ""),
            )
        console.print(t)
    console.print()


@app.command("test")
def test_channel(
    channel: str = typer.Argument(..., help="Channel name (telegram, slack)"),
    message: str = typer.Argument(..., help="Text to send"),
    chat_id: str = typer.Option("", "--chat-id", "-c", help="Target chat ID"),
):
    """Send a test message via a channel (requires token in config)."""
    async def _run():
        import seraphim.channels.telegram  # noqa: F401
        import seraphim.channels.slack     # noqa: F401
        from seraphim.channels.base import ChannelRegistry
        from seraphim.channels.handler import handle_channel_message
        try:
            cls = ChannelRegistry.get(channel)
        except KeyError:
            available = ChannelRegistry.list_names()
            console.print(f"[red]✗[/red] Channel '{channel}' not found. Available: {available}")
            return
        ch = cls()
        await ch.start(handle_channel_message)
        cid = chat_id or "test"
        await ch.send(cid, message)
        await ch.stop()
        console.print(f"[green]✓[/green] Sent to {channel}:{cid}")

    asyncio.run(_run())

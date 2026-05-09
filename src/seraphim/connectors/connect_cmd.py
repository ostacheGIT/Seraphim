"""Top-level `seraphim connect` CLI — manage external service connections."""
from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="connect", help="Connect external services (Google, etc.).")
console = Console()


@app.command("google")
def connect_google(
    client_id: Optional[str] = typer.Option(None, "--client-id", help="Google OAuth client ID"),
    client_secret: Optional[str] = typer.Option(None, "--client-secret", help="Google OAuth client secret"),
):
    """Connect Gmail and Google Calendar via OAuth 2.0.

    Prerequisites:
      1. Go to https://console.cloud.google.com/ → APIs & Services → Credentials
      2. Create OAuth 2.0 Client ID (Desktop app type)
      3. Enable Gmail API and Google Calendar API in the API Library
      4. Run: seraphim connect google --client-id <id> --client-secret <secret>
    """
    from seraphim.connectors.oauth import (
        get_client_credentials,
        is_connected,
        run_oauth_flow,
        save_client_credentials,
    )

    if client_id and client_secret:
        save_client_credentials(client_id, client_secret)
        console.print("[dim]Client credentials saved.[/dim]")
    else:
        # Try loading existing credentials
        try:
            get_client_credentials()
        except RuntimeError as e:
            console.print(f"[red]✗[/red] {e}")
            console.print("\n[dim]Provide credentials with:[/dim]")
            console.print("  seraphim connect google --client-id <id> --client-secret <secret>")
            raise typer.Exit(1)

    if is_connected():
        console.print("[yellow]Already connected. Re-authenticating...[/yellow]")

    try:
        run_oauth_flow()
        console.print("[green]✓[/green] Gmail and Google Calendar connected.")
        console.print("[dim]Run [bold]seraphim digest run[/bold] to test.[/dim]")
    except Exception as e:
        console.print(f"[red]✗[/red] Authentication failed: {e}")
        raise typer.Exit(1)


@app.command("status")
def connect_status():
    """Show connection status for all external services."""
    from seraphim.connectors.oauth import is_connected, load_tokens
    import time

    table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    table.add_column("Service")
    table.add_column("Status")
    table.add_column("Details")

    connected = is_connected()
    tokens = load_tokens() if connected else None

    if connected and tokens:
        expiry = tokens.get("expiry", 0)
        remaining = expiry - time.time()
        if remaining > 0:
            detail = f"token expires in {int(remaining // 60)}m"
        else:
            detail = "token expired — will auto-refresh"
        status = "[green]● Connected[/green]"
    else:
        status = "[red]● Disconnected[/red]"
        detail = "run: seraphim connect google"

    table.add_row("Gmail", status, detail)
    table.add_row("Google Calendar", status, detail)

    console.print()
    console.print(table)

    if not connected:
        console.print()
        console.print("[dim]→ [bold]seraphim connect google --client-id <id> --client-secret <secret>[/bold][/dim]")

    # Check what digest will fetch
    console.print()
    if connected:
        _show_live_check()


def _show_live_check() -> None:
    """Quick live API ping to confirm tokens actually work."""
    async def _ping():
        try:
            from seraphim.connectors.gmail import gmail_connector
            from seraphim.connectors.gcalendar import gcalendar_connector

            with console.status("[dim]Pinging Gmail...[/dim]"):
                unread = gmail_connector.get_unread_count()
            console.print(f"  Gmail   : [green]✓[/green] {unread} unread today")

            with console.status("[dim]Pinging Calendar...[/dim]"):
                events = gcalendar_connector.get_today_events()
            console.print(f"  Calendar: [green]✓[/green] {len(events)} events today")

        except Exception as e:
            console.print(f"  [red]API ping failed: {e}[/red]")
            console.print("  [dim]Token may be invalid — run: seraphim connect google[/dim]")

    asyncio.run(_ping())


@app.command("disconnect")
def connect_disconnect(
    service: str = typer.Argument("google", help="Service to disconnect: google"),
):
    """Disconnect an external service and remove stored tokens."""
    if service != "google":
        console.print(f"[red]Unknown service: {service!r}. Supported: google[/red]")
        raise typer.Exit(1)

    from seraphim.connectors.oauth import delete_tokens, is_connected

    if not is_connected():
        console.print("[yellow]Google not connected.[/yellow]")
        return

    delete_tokens()
    console.print("[green]✓[/green] Google tokens removed. Gmail and Calendar disconnected.")

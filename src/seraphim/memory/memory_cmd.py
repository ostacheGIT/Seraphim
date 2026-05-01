"""CLI subcommand: seraphim memory — index, search, stats, clear."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="memory", help="Manage the RAG knowledge base.", add_completion=False)
console = Console()


def _get_backend():
    from seraphim.memory import SQLiteFTSMemory
    from seraphim.memory import get_rag_backend, set_rag_backend

    backend = get_rag_backend()
    if backend is None:
        backend = SQLiteFTSMemory()
        set_rag_backend(backend)
    return backend


@app.command("index")
def index_cmd(
    path: Path = typer.Argument(..., help="File or directory to index"),
    backend: str = typer.Option("sqlite_fts", "--backend", "-b", help="sqlite_fts | faiss | bm25 | hybrid"),
    chunk_size: int = typer.Option(512, "--chunk-size", help="Tokens per chunk"),
    chunk_overlap: int = typer.Option(64, "--overlap", help="Overlap tokens between chunks"),
    extensions: Optional[str] = typer.Option(
        None, "--ext", help="Comma-separated extensions, e.g. .txt,.md,.pdf"
    ),
):
    """Index a file or directory into the knowledge base."""
    from seraphim.memory import create_backend, ingest_directory, ingest_file, set_rag_backend
    from seraphim.memory.chunking import ChunkConfig

    mem = create_backend(backend)
    set_rag_backend(mem)
    cfg = ChunkConfig(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    path = Path(path).expanduser().resolve()
    if not path.exists():
        console.print(f"[red]Path not found: {path}[/red]")
        raise typer.Exit(1)

    with console.status(f"[dim]Indexing {path}...[/dim]"):
        if path.is_file():
            ids = ingest_file(path, mem, config=cfg)
            count = len(ids)
        else:
            exts = [e.strip() for e in extensions.split(",")] if extensions else None
            count = ingest_directory(path, mem, extensions=exts, config=cfg)

    console.print(f"[green]✓[/green] Indexed [bold]{count}[/bold] chunks from [cyan]{path}[/cyan]")

    # Persist RAG-enabled flag in config
    _enable_rag()


@app.command("search")
def search_cmd(
    query: str = typer.Argument(..., help="Search query"),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Number of results"),
):
    """Search the knowledge base."""
    backend = _get_backend()
    results = backend.retrieve(query, top_k=top_k)

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        return

    table = Table(title=f"Results for: {query}", show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Score", width=7)
    table.add_column("Source", width=20)
    table.add_column("Content")

    for i, r in enumerate(results, 1):
        table.add_row(
            str(i),
            f"{r.score:.3f}",
            r.source or "—",
            r.content[:200] + ("…" if len(r.content) > 200 else ""),
        )

    console.print(table)


@app.command("stats")
def stats_cmd():
    """Show knowledge base statistics."""
    backend = _get_backend()
    count = getattr(backend, "count", lambda: "?")()
    console.print(f"[bold]RAG backend:[/bold] {backend.backend_id}")
    console.print(f"[bold]Stored chunks:[/bold] {count}")


@app.command("clear")
def clear_cmd(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Clear all indexed content from the knowledge base."""
    if not yes:
        confirmed = typer.confirm("Clear all indexed content?")
        if not confirmed:
            console.print("[dim]Cancelled.[/dim]")
            return
    backend = _get_backend()
    backend.clear()
    console.print("[green]✓[/green] Knowledge base cleared.")


def _enable_rag() -> None:
    """Update config.yaml to set rag_enabled: true."""
    from pathlib import Path as P
    import yaml

    candidates = [
        P("configs/seraphim/config.yaml"),
        P.home() / ".seraphim" / "config.yaml",
    ]
    for cfg_path in candidates:
        if cfg_path.exists():
            try:
                data = yaml.safe_load(cfg_path.read_text()) or {}
                memory_cfg = data.setdefault("memory", {})
                memory_cfg["rag_enabled"] = True
                cfg_path.write_text(yaml.dump(data, allow_unicode=True))
            except Exception:
                pass
            return
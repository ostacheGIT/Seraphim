"""Commandes CLI pour la gestion des skills externes Seraphim.

Usage :
    seraphim skill list [--source hermes|openclaw|github] [--category CAT]
    seraphim skill search QUERY [--source ...]
    seraphim skill import NAME [--source ...] [--with-scripts] [--force]
    seraphim skill sync [--source ...]
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="skill", help="Gérer les skills externes Seraphim.")
console = Console()

_SOURCE_CHOICES = ["hermes", "openclaw", "skillssh", "voltagent", "leoye", "autonomys", "github"]


def _make_resolver(source: str, github_url: Optional[str] = None):
    """Instancie le bon résolveur selon la source."""
    from seraphim.skills.sources import HermesResolver, OpenClawResolver, GitHubResolver, SkillsShResolver

    if source == "hermes":
        return HermesResolver()
    if source == "openclaw":
        return OpenClawResolver()
    if source == "skillssh":
        return SkillsShResolver()
    if source == "voltagent":
        cache = Path.home() / ".seraphim" / "skill-cache" / "voltagent"
        return GitHubResolver(cache_root=cache, repo_url="https://github.com/VoltAgent/awesome-openclaw-skills")
    if source == "leoye":
        cache = Path.home() / ".seraphim" / "skill-cache" / "leoye"
        return GitHubResolver(cache_root=cache, repo_url="https://github.com/LeoYeAI/openclaw-master-skills")
    if source == "autonomys":
        cache = Path.home() / ".seraphim" / "skill-cache" / "autonomys"
        return GitHubResolver(cache_root=cache, repo_url="https://github.com/autonomys/openclaw-skills")
    if source == "github":
        if not github_url:
            console.print("[red]✗ --github-url requis pour la source 'github'[/red]")
            raise typer.Exit(1)
        cache = Path.home() / ".seraphim" / "skill-cache" / "github"
        return GitHubResolver(cache_root=cache, repo_url=github_url)
    console.print(f"[red]✗ Source inconnue : {source}[/red]")
    raise typer.Exit(1)


def _make_importer():
    from seraphim.skills.parser import SkillParser
    from seraphim.skills.tool_translator import ToolTranslator
    from seraphim.skills.importer import SkillImporter

    return SkillImporter(
        parser=SkillParser(),
        tool_translator=ToolTranslator(),
    )


@app.command("sync")
def skill_sync(
        source: str = typer.Option("hermes", "--source", "-s", help="Source : hermes, openclaw, github"),
        github_url: Optional[str] = typer.Option(None, "--github-url", help="URL du dépôt GitHub"),
):
    """Synchronise le cache local depuis une source externe."""
    resolver = _make_resolver(source, github_url)
    console.print(f"[dim]Synchronisation depuis {source}...[/dim]")
    try:
        resolver.sync()
        console.print(f"[green]✓[/green] Cache synchronisé : {resolver.cache_dir()}")
    except Exception as exc:
        console.print(f"[red]✗ Erreur lors de la synchronisation : {exc}[/red]")
        raise typer.Exit(1)


@app.command("list")
def skill_list(
        source: str = typer.Option("hermes", "--source", "-s", help="Source : hermes, openclaw, github"),
        github_url: Optional[str] = typer.Option(None, "--github-url", help="URL du dépôt GitHub"),
        category: Optional[str] = typer.Option(None, "--category", "-c", help="Filtrer par catégorie"),
):
    """Liste les skills disponibles dans une source."""
    resolver = _make_resolver(source, github_url)

    if category:
        skills = resolver.filter_by_category(category)
    else:
        skills = resolver.list_skills()

    if not skills:
        console.print(f"[yellow]Aucun skill trouvé dans {source}[/yellow]")
        console.print("[dim]Essayez d'abord : seraphim skill sync[/dim]")
        return

    table = Table(title=f"Skills — {source}", show_header=True, header_style="bold cyan")
    table.add_column("Nom", style="cyan")
    table.add_column("Catégorie", style="dim")
    table.add_column("Description")

    for s in skills:
        desc = s.description[:80] + "..." if len(s.description) > 80 else s.description
        table.add_row(s.name, s.category, desc)

    console.print(table)
    console.print(f"\n[dim]{len(skills)} skill(s) trouvé(s)[/dim]")


@app.command("search")
def skill_search(
        query: str = typer.Argument(..., help="Terme de recherche"),
        source: str = typer.Option("hermes", "--source", "-s", help="Source : hermes, openclaw, github"),
        github_url: Optional[str] = typer.Option(None, "--github-url", help="URL du dépôt GitHub"),
):
    """Recherche un skill par nom dans une source."""
    resolver = _make_resolver(source, github_url)
    skills = resolver.resolve(query)

    if not skills:
        console.print(f"[yellow]Aucun skill correspondant à '{query}' dans {source}[/yellow]")
        return

    table = Table(title=f"Résultats pour '{query}'", show_header=True, header_style="bold cyan")
    table.add_column("Nom", style="cyan")
    table.add_column("Catégorie", style="dim")
    table.add_column("Description")

    for s in skills:
        desc = s.description[:80] + "..." if len(s.description) > 80 else s.description
        table.add_row(s.name, s.category, desc)

    console.print(table)


@app.command("build-index")
def skill_build_index():
    """Construit l'index de recherche des skills (requis après sync-all)."""
    from seraphim.skills.catalog import build_catalog, get_catalog_size

    counts: dict[str, int] = {}

    def _cb(source: str, n: int):
        counts[source] = n
        console.print(f"  [dim]{source}:[/dim] {n} skills scannés")

    console.print("[dim]Construction de l'index...[/dim]")
    total = build_catalog(progress_callback=_cb)
    console.print(
        f"\n[bold green]✓ {total} skills indexés[/bold green] → "
        f"[dim]~/.seraphim/skill-catalog.json[/dim]\n"
        "Les skills sont maintenant disponibles automatiquement dans Seraphim."
    )


@app.command("sync-all")
def skill_sync_all():
    """Synchronise OpenClaw (~50 skills) et Hermes (~150 skills) en une commande."""
    for source in ["openclaw", "hermes", "skillssh", "voltagent", "leoye", "autonomys"] :
        resolver = _make_resolver(source)
        console.print(f"[dim]Sync {source}...[/dim]", end=" ")
        try:
            resolver.sync()
            console.print(f"[green]✓[/green]  {resolver.cache_dir()}")
        except Exception as exc:
            console.print(f"[red]✗ {exc}[/red]")
    # Auto-build index after sync
    console.print("\n[dim]Construction de l'index de recherche...[/dim]")
    try:
        from seraphim.skills.catalog import build_catalog
        total = build_catalog()
        console.print(f"[green]✓[/green] Index : {total} skills indexés")
    except Exception as exc:
        console.print(f"[yellow]⚠ Index non construit : {exc}[/yellow]")

    console.print(
        "\n[bold green]✓ Prêt.[/bold green] "
        "Seraphim utilise maintenant les skills automatiquement selon votre requête.\n"
        "Recherche manuelle : [bold]seraphim skill search <terme> --source openclaw[/bold]"
    )


@app.command("install-all")
def skill_install_all(
        source: str = typer.Option("openclaw", "--source", "-s", help="Source : hermes | openclaw"),
        force: bool = typer.Option(False, "--force", "-f", help="Écraser les skills déjà installés"),
):
    """Importe tous les skills d'une source dans ~/.seraphim/skills/ (peut être long)."""
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

    resolver = _make_resolver(source)
    importer = _make_importer()

    console.print(f"[dim]Chargement de la liste des skills depuis {source}...[/dim]")
    try:
        all_skills = resolver.list_skills()
    except Exception as exc:
        console.print(f"[red]✗ {exc}[/red]")
        console.print(f"[dim]Sync d'abord : seraphim skill sync --source {source}[/dim]")
        raise typer.Exit(1)

    if not all_skills:
        console.print(f"[yellow]Aucun skill dans {source}. Essayez : seraphim skill sync --source {source}[/yellow]")
        raise typer.Exit(1)

    ok = err = skip = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"Import {source}…", total=len(all_skills))
        for skill in all_skills:
            result = importer.import_skill(skill, force=force)
            if result.skipped:
                skip += 1
            elif result.success:
                ok += 1
            else:
                err += 1
            progress.advance(task)

    console.print(
        f"\n[green]✓ {ok} importés[/green]  "
        f"[dim]{skip} ignorés (déjà installés)[/dim]  "
        f"{'[red]' + str(err) + ' erreurs[/red]' if err else ''}"
    )


@app.command("import")
def skill_import(
        name: str = typer.Argument(..., help="Nom du skill à importer"),
        source: str = typer.Option("hermes", "--source", "-s", help="Source : hermes, openclaw, github"),
        github_url: Optional[str] = typer.Option(None, "--github-url", help="URL du dépôt GitHub"),
        with_scripts: bool = typer.Option(False, "--with-scripts", help="Inclure le répertoire scripts/"),
        force: bool = typer.Option(False, "--force", "-f", help="Écraser si déjà installé"),
):
    """Importe un skill depuis une source externe dans ~/.seraphim/skills/."""
    resolver = _make_resolver(source, github_url)
    importer = _make_importer()

    # Recherche du skill
    matches = resolver.resolve(name)
    if not matches:
        console.print(f"[red]✗ Skill '{name}' introuvable dans {source}[/red]")
        console.print("[dim]Essayez : seraphim skill search <terme>[/dim]")
        raise typer.Exit(1)

    # Correspondance exacte en priorité
    exact = [s for s in matches if s.name == name]
    resolved = exact[0] if exact else matches[0]

    if len(matches) > 1 and not exact:
        console.print(f"[yellow]Plusieurs skills correspondent à '{name}'. Utilisation de : {resolved.name}[/yellow]")

    console.print(f"[dim]Importation de [bold]{resolved.name}[/bold] depuis {source}...[/dim]")

    result = importer.import_skill(resolved, with_scripts=with_scripts, force=force)

    if result.skipped:
        console.print(f"[yellow]⚠ Skill déjà installé[/yellow] : {result.target_path}")
        console.print("[dim]Utilisez --force pour écraser[/dim]")
        return

    if not result.success:
        console.print(f"[red]✗ Échec de l'importation :[/red]")
        for w in result.warnings:
            console.print(f"  [red]{w}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]✓[/green] Skill [bold]{resolved.name}[/bold] installé dans {result.target_path}")

    if result.translated_tools:
        console.print(f"  [dim]Outils traduits : {', '.join(result.translated_tools)}[/dim]")
    if result.untranslated_tools:
        console.print(f"  [yellow]⚠ Outils non traduits : {', '.join(result.untranslated_tools)}[/yellow]")
    if result.scripts_imported:
        console.print("  [green]✓[/green] Scripts importés")
    for w in result.warnings:
        console.print(f"  [yellow]⚠ {w}[/yellow]")

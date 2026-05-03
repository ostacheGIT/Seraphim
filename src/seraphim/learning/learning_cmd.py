"""CLI commands for the learning loop: `seraphim learn`."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="learn", help="Learning loop — collect traces, mine SFT pairs, optimize prompts.")
daemon_app = typer.Typer(name="daemon", help="Background learning daemon — runs the learning loop automatically.")
app.add_typer(daemon_app)
console = Console()


@app.command("status")
def status():
    """Show trace and learning stats."""
    async def _run():
        from seraphim.learning.trace_store import trace_stats
        s = await trace_stats()
        console.print("\n[bold]Learning stats[/bold]")
        console.print(f"  Traces total   : [cyan]{s['total_traces']}[/cyan]")
        console.print(f"  Good traces    : [green]{s['good_traces']}[/green]")
        console.print(f"  SFT pairs      : [yellow]{s['sft_pairs']}[/yellow]")
        console.print(f"  Active overlays: [magenta]{s['accepted_overlays']}[/magenta]")
        console.print()
    asyncio.run(_run())


@app.command("mine")
def mine_cmd(
    agent: str = typer.Option("", "--agent", "-a", help="Filter by agent (empty = all)"),
    min_quality: float = typer.Option(0.6, "--quality", "-q", help="Min quality threshold"),
):
    """Extract SFT training pairs from accumulated traces."""
    async def _run():
        from seraphim.learning.miner import mine
        console.print(f"[dim]Mining SFT pairs (agent={agent or 'all'}, min_quality={min_quality})...[/dim]")
        n = await mine(agent=agent or None, min_quality=min_quality)
        console.print(f"[green]✓[/green] Mined [bold]{n}[/bold] SFT pairs.")
    asyncio.run(_run())


@app.command("export")
def export_cmd(
    output: str = typer.Option("~/.seraphim/sft_pairs.jsonl", "--output", "-o"),
    agent: str = typer.Option("", "--agent", "-a"),
    min_quality: float = typer.Option(0.6, "--quality", "-q"),
):
    """Export SFT pairs as JSONL for fine-tuning."""
    async def _run():
        from seraphim.learning.miner import export_jsonl
        path = str(Path(output).expanduser())
        n = await export_jsonl(path, agent=agent or None, min_quality=min_quality)
        console.print(f"[green]✓[/green] Exported [bold]{n}[/bold] pairs to [cyan]{path}[/cyan]")
    asyncio.run(_run())


@app.command("optimize")
def optimize_cmd(
    agents: str = typer.Option("react,chat", "--agents", "-a", help="Comma-separated agent names"),
    min_quality: float = typer.Option(0.65, "--quality", "-q"),
    max_examples: int = typer.Option(5, "--examples", "-e"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Build overlays but don't save"),
):
    """Optimize agent prompts from trace data and evaluate."""
    async def _run():
        from seraphim.learning.orchestrator import LearningConfig, run_learning_loop
        cfg = LearningConfig(
            agents=[a.strip() for a in agents.split(",")],
            min_quality=min_quality,
            max_examples=max_examples,
            dry_run=dry_run,
        )
        console.print("[bold]Running optimization loop...[/bold]")
        result = await run_learning_loop(cfg)

        console.print(f"\n[bold]Results[/bold]")
        console.print(f"  SFT pairs mined : [yellow]{result.mined_pairs}[/yellow]")
        console.print(f"  Overlays accepted: [green]{result.accepted}[/green]")
        console.print(f"  Overlays rejected: [red]{result.rejected}[/red]")

        if result.overlays:
            t = Table(show_header=True)
            t.add_column("Agent")
            t.add_column("Before")
            t.add_column("After")
            t.add_column("Δ")
            t.add_column("Status")
            for ov in result.overlays:
                delta = ov["score_after"] - ov["score_before"]
                status = "[green]ACCEPT[/green]" if ov["accepted"] else "[red]REJECT[/red]"
                t.add_row(
                    ov["agent"],
                    f"{ov['score_before']:.3f}",
                    f"{ov['score_after']:.3f}",
                    f"{delta:+.3f}",
                    status,
                )
            console.print(t)

        if dry_run:
            console.print("[dim](dry-run — overlays not saved)[/dim]")

    asyncio.run(_run())


@app.command("finetune")
def finetune_cmd(
    base_model: str = typer.Option("Qwen/Qwen2.5-3B-Instruct", "--model", "-m", help="HuggingFace model ID"),
    sft_path: str = typer.Option("~/.seraphim/sft_pairs.jsonl", "--sft", "-s", help="JSONL SFT pairs path"),
    output_dir: str = typer.Option("~/.seraphim/lora_adapter", "--output", "-o"),
    epochs: int = typer.Option(3, "--epochs", "-e"),
    lora_r: int = typer.Option(16, "--lora-r", help="LoRA rank"),
    ollama_name: str = typer.Option("seraphim-tuned", "--name", "-n", help="Ollama model name after export"),
    no_merge: bool = typer.Option(False, "--no-merge", help="Skip adapter merge"),
    no_ollama: bool = typer.Option(False, "--no-ollama", help="Skip Ollama model creation"),
    use_unsloth: bool = typer.Option(False, "--unsloth", help="Use unsloth for faster training"),
    check: bool = typer.Option(False, "--check", help="Only check if dependencies are installed"),
):
    """Run LoRA fine-tuning on local model from SFT pairs."""
    if check:
        from seraphim.learning.finetuner import check_deps
        missing = check_deps()
        if missing:
            console.print(f"[red]✗[/red] Missing: {', '.join(missing)}")
            console.print(f"  Install: [bold]pip install {' '.join(missing)}[/bold]")
        else:
            console.print("[green]✓[/green] All fine-tuning dependencies installed.")
        return

    async def _run():
        from seraphim.learning.finetuner import FineTuneConfig, run_lora_finetune
        cfg = FineTuneConfig(
            base_model=base_model,
            sft_path=sft_path,
            output_dir=output_dir,
            epochs=epochs,
            lora_r=lora_r,
            ollama_model_name=ollama_name,
            merge_adapter=not no_merge,
            push_to_ollama=not no_ollama,
            use_unsloth=use_unsloth,
        )
        console.print(f"[bold]LoRA fine-tuning[/bold] — {base_model} — {epochs} epochs")
        console.print(f"  SFT pairs : [cyan]{sft_path}[/cyan]")
        console.print(f"  Output    : [cyan]{output_dir}[/cyan]")
        console.print(f"  Merge     : {'no' if no_merge else 'yes'}")
        console.print(f"  → Ollama  : {'no' if no_ollama else ollama_name}\n")
        with console.status("[dim]Training... (this may take a while)[/dim]"):
            result = await run_lora_finetune(cfg)
        if result.success:
            console.print(f"[green]✓[/green] Done. Loss: [yellow]{result.train_loss:.4f}[/yellow]")
            console.print(f"  Adapter   : [cyan]{result.output_dir}[/cyan]")
            if result.merged_dir:
                console.print(f"  Merged    : [cyan]{result.merged_dir}[/cyan]")
            if result.ollama_model:
                console.print(f"  Ollama    : [green]{result.ollama_model}[/green]")
                console.print(
                    f'\n  Use: [bold]seraphim ask --engine {result.ollama_model} "Hello!"[/bold]'
                )
            elif not result.merged_dir and not no_merge:
                console.print(
                    "[yellow]⚠[/yellow] Merge skipped (not enough RAM). "
                    "Adapter saved — run merge on a machine with ≥16GB RAM."
                )
            elif not no_ollama:
                console.print(
                    "[yellow]⚠[/yellow] Ollama export skipped — install llama.cpp and ensure "
                    "convert_hf_to_gguf.py is on PATH."
                )
        else:
            console.print(f"[red]✗[/red] {result.message}")
    asyncio.run(_run())


@app.command("run")
def run_cmd(
    agents: str = typer.Option("react,chat", "--agents", "-a"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    grpo: bool = typer.Option(False, "--grpo", "-g", help="Run GRPO sampling before SFT mining"),
    grpo_generations: int = typer.Option(4, "--grpo-g", help="GRPO generations per prompt"),
    finetune: bool = typer.Option(False, "--finetune", "-f", help="Run LoRA fine-tuning after optimize"),
):
    """Full learning loop: [GRPO →] mine → optimize → eval → accept/reject [→ finetune]."""
    async def _run():
        from seraphim.learning.orchestrator import LearningConfig, run_learning_loop
        cfg = LearningConfig(
            agents=[a.strip() for a in agents.split(",")],
            dry_run=dry_run,
            run_grpo=grpo,
            grpo_generations=grpo_generations,
            run_finetune=finetune and not dry_run,
        )
        console.print(
            f"[bold]Full learning loop[/bold] — agents={agents}"
            + (" [GRPO]" if grpo else "")
            + (" [finetune]" if finetune else "")
        )
        result = await run_learning_loop(cfg)

        if result.grpo_result:
            g = result.grpo_result
            console.print(
                f"  GRPO: prompts=[cyan]{g['prompts_used']}[/cyan] "
                f"generations=[cyan]{g['total_generations']}[/cyan] "
                f"reward=[yellow]{g['mean_reward']:.3f}[/yellow] "
                f"pairs=[green]{g['pairs_saved']}[/green]"
            )
        console.print(f"  SFT pairs mined : [yellow]{result.mined_pairs}[/yellow]")
        console.print(f"  Overlays accepted: [green]{result.accepted}[/green]")
        console.print(f"  Overlays rejected: [red]{result.rejected}[/red]")
        if result.finetune_result:
            ft = result.finetune_result
            s = "[green]✓[/green]" if ft["success"] else "[red]✗[/red]"
            console.print(f"  Finetune: {s} loss={ft.get('train_loss', '?')}")

    asyncio.run(_run())


@app.command("grpo")
def grpo_cmd(
    agent: str = typer.Option("", "--agent", "-a", help="Filter by agent (empty = all)"),
    generations: int = typer.Option(4, "--generations", "-g", help="Responses per prompt"),
    max_prompts: int = typer.Option(30, "--max-prompts", "-n", help="Max prompts per run"),
    threshold: float = typer.Option(0.0, "--threshold", "-t", help="Min advantage to save pair"),
    backprop: bool = typer.Option(False, "--backprop", "-b", help="Run HuggingFace GRPO training"),
    base_model: str = typer.Option("Qwen/Qwen2.5-3B-Instruct", "--model", "-m"),
    check: bool = typer.Option(False, "--check", help="Check backprop dependencies"),
):
    """GRPO: sample N responses per prompt, score with LLM-judge, store winners."""
    if check:
        from seraphim.learning.grpo_trainer import check_backprop_deps
        missing = check_backprop_deps()
        if missing:
            console.print(f"[red]✗[/red] Missing (backprop): {', '.join(missing)}")
            console.print(f"  Install: [bold]pip install {' '.join(missing)}[/bold]")
        else:
            console.print("[green]✓[/green] All GRPO backprop dependencies installed.")
        return

    async def _run():
        from seraphim.learning.grpo_trainer import GRPOConfig, run_grpo
        cfg = GRPOConfig(
            num_generations=generations,
            min_prompts=1,
            max_prompts=max_prompts,
            agent=agent,
            advantage_threshold=threshold,
            run_backprop=backprop,
            base_model=base_model,
        )
        console.print(
            f"[bold]GRPO sampling[/bold] — {generations} generations × "
            f"(up to {max_prompts} prompts)"
            + (f" — agent=[cyan]{agent}[/cyan]" if agent else "")
        )
        if backprop:
            console.print(f"  [yellow]+ backprop enabled[/yellow] — model: {base_model}")

        with console.status("[dim]Sampling & scoring...[/dim]"):
            result = await run_grpo(cfg)

        if not result.success:
            console.print(f"[red]✗[/red] {result.message}")
            return

        console.print(f"\n[bold]GRPO results[/bold]")
        console.print(f"  Prompts used      : [cyan]{result.prompts_used}[/cyan]")
        console.print(f"  Total generations : [cyan]{result.total_generations}[/cyan]")
        console.print(f"  Mean reward       : [yellow]{result.mean_reward:.3f}[/yellow]")
        console.print(f"  Mean adv (saved)  : [yellow]{result.mean_advantage_saved:+.3f}[/yellow]")
        console.print(f"  New SFT pairs     : [green]{result.pairs_saved}[/green]")
        if backprop:
            status_str = "[green]✓[/green]" if result.backprop_done else "[red]✗[/red]"
            console.print(f"  Backprop          : {status_str}")
            if result.output_dir:
                console.print(f"  Adapter           : [cyan]{result.output_dir}[/cyan]")
        console.print(f"\n[dim]{result.message}[/dim]")

    asyncio.run(_run())


@app.command("feedback")
def feedback_cmd(
    trace_id: str = typer.Argument(..., help="Trace ID"),
    score: float = typer.Argument(..., help="Score 0.0–1.0"),
):
    """Set explicit feedback score on a trace."""
    async def _run():
        from seraphim.learning.trace_store import set_feedback
        await set_feedback(trace_id, score)
        console.print(f"[green]✓[/green] Feedback {score:.2f} set on trace [dim]{trace_id}[/dim]")
    asyncio.run(_run())


_PID_FILE = Path.home() / ".seraphim" / "learn_watch.pid"


@app.command("watch")
def watch_cmd(
    interval: float = typer.Option(6.0, "--interval", "-i", help="Hours between learning loop runs"),
    agents: str = typer.Option("react,chat", "--agents", "-a"),
    min_quality: float = typer.Option(0.6, "--quality", "-q"),
    finetune: bool = typer.Option(False, "--finetune", "-f", help="Include LoRA fine-tune step"),
    min_new_traces: int = typer.Option(3, "--min-traces", help="Min new traces to trigger a run"),
    daemon: bool = typer.Option(False, "--daemon", "-d", help="Run as background process"),
):
    """Run learning loop continuously, every --interval hours."""
    if daemon:
        _start_daemon(interval, agents, min_quality, finetune, min_new_traces)
        return

    async def _loop():
        from seraphim.learning.orchestrator import LearningConfig, run_learning_loop
        from seraphim.learning.trace_store import trace_stats

        agent_list = [a.strip() for a in agents.split(",")]
        interval_secs = interval * 3600
        run_count = 0

        _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PID_FILE.write_text(str(os.getpid()))

        console.print(f"[bold cyan]Learning watch[/bold cyan] started — interval={interval}h agents={agents}")
        console.print(f"  PID {os.getpid()} — Ctrl+C to stop\n")

        try:
            while True:
                stats = await trace_stats()
                last_total = stats["total_traces"]

                next_run = datetime.now() + timedelta(seconds=interval_secs)
                console.print(
                    f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] "
                    f"Sleeping until [cyan]{next_run.strftime('%H:%M:%S')}[/cyan] "
                    f"(traces={last_total})"
                )

                await asyncio.sleep(interval_secs)

                # Check enough new traces accumulated
                stats_now = await trace_stats()
                new_traces = stats_now["total_traces"] - last_total
                if new_traces < min_new_traces:
                    console.print(
                        f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] "
                        f"Only {new_traces} new traces (need {min_new_traces}) — skipping"
                    )
                    continue

                run_count += 1
                console.print(
                    f"\n[bold]{datetime.now().strftime('%H:%M:%S')}[/bold] "
                    f"Run #{run_count} — {new_traces} new traces"
                )

                cfg = LearningConfig(
                    agents=agent_list,
                    min_quality=min_quality,
                    run_finetune=finetune,
                )
                result = await run_learning_loop(cfg)

                console.print(
                    f"  mined=[yellow]{result.mined_pairs}[/yellow] "
                    f"accepted=[green]{result.accepted}[/green] "
                    f"rejected=[red]{result.rejected}[/red]"
                )
                if result.finetune_result:
                    ft = result.finetune_result
                    status = "[green]✓[/green]" if ft["success"] else "[red]✗[/red]"
                    loss = f"loss={ft['train_loss']:.4f}" if ft.get("train_loss") else ""
                    console.print(f"  finetune={status} {loss}")
                console.print()

        except asyncio.CancelledError:
            pass
        except KeyboardInterrupt:
            pass
        finally:
            if _PID_FILE.exists():
                _PID_FILE.unlink()
            console.print("\n[dim]Learning watch stopped.[/dim]")

    try:
        asyncio.run(_loop())
    except KeyboardInterrupt:
        pass


def _start_daemon(interval, agents, min_quality, finetune, min_new_traces):
    """Spawn watch as a detached background process."""
    if _PID_FILE.exists():
        pid = _PID_FILE.read_text().strip()
        console.print(f"[yellow]⚠[/yellow] Watch already running (PID {pid}). Run 'seraphim learn stop' first.")
        return

    cmd = [
        sys.executable, "-m", "seraphim.cli",
        "learn", "watch",
        "--interval", str(interval),
        "--agents", agents,
        "--quality", str(min_quality),
        "--min-traces", str(min_new_traces),
    ]
    if finetune:
        cmd.append("--finetune")

    log_path = Path.home() / ".seraphim" / "learn_watch.log"
    log_file = open(log_path, "a")

    kwargs = dict(stdout=log_file, stderr=log_file)
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **kwargs)
    console.print(f"[green]✓[/green] Learning watch started in background (PID {proc.pid})")
    console.print(f"  Log: [cyan]{log_path}[/cyan]")
    console.print(f"  Stop: [bold]seraphim learn stop[/bold]")


@app.command("stop")
def stop_cmd():
    """Stop the background learning watch daemon."""
    if not _PID_FILE.exists():
        console.print("[yellow]⚠[/yellow] No watch daemon running.")
        return
    pid_str = _PID_FILE.read_text().strip()
    try:
        pid = int(pid_str)
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
        _PID_FILE.unlink(missing_ok=True)
        console.print(f"[green]✓[/green] Learning watch (PID {pid}) stopped.")
    except (ValueError, ProcessLookupError):
        _PID_FILE.unlink(missing_ok=True)
        console.print(f"[yellow]⚠[/yellow] Process {pid_str} not found — PID file removed.")
    except PermissionError:
        console.print(f"[red]✗[/red] Permission denied killing PID {pid_str}.")


@daemon_app.command("start")
def daemon_start(
    interval: float = typer.Option(6.0, "--interval", "-i", help="Hours between runs"),
    agents: str = typer.Option("react,chat", "--agents", "-a"),
    min_quality: float = typer.Option(0.6, "--quality", "-q"),
    min_new_traces: int = typer.Option(3, "--min-traces", help="Min new traces to trigger a run"),
    grpo: bool = typer.Option(False, "--grpo", "-g", help="Run GRPO sampling each cycle"),
    grpo_generations: int = typer.Option(4, "--grpo-g", help="GRPO generations per prompt"),
    grpo_max_prompts: int = typer.Option(30, "--grpo-n", help="GRPO max prompts per run"),
    finetune: bool = typer.Option(False, "--finetune", "-f", help="Run LoRA fine-tune each cycle"),
    auto_start: bool = typer.Option(False, "--auto-start", help="Auto-start daemon when Seraphim server starts"),
):
    """Start the learning daemon in the background."""
    from seraphim.learning.daemon import PID_FILE, CONFIG_FILE, is_alive, read_state

    state = read_state()
    if PID_FILE.exists():
        pid_str = PID_FILE.read_text().strip()
        try:
            pid = int(pid_str)
            if is_alive(pid):
                console.print(f"[yellow]⚠[/yellow]  Daemon already running (PID {pid}). "
                               f"Use [bold]seraphim learn daemon stop[/bold] first.")
                return
        except ValueError:
            pass
        PID_FILE.unlink(missing_ok=True)

    config = {
        "interval_hours": interval,
        "agents": agents,
        "min_quality": min_quality,
        "min_new_traces": min_new_traces,
        "run_grpo": grpo,
        "grpo_generations": grpo_generations,
        "grpo_max_prompts": grpo_max_prompts,
        "run_finetune": finetune,
        "auto_start": auto_start,
    }
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))

    from seraphim.learning.daemon import LOG_FILE
    log_file = open(LOG_FILE, "a")

    # On Windows use pythonw.exe (no console window); fall back to python.exe if missing
    if sys.platform == "win32":
        from pathlib import Path as _Path
        pythonw = _Path(sys.executable).parent / "pythonw.exe"
        executable = str(pythonw) if pythonw.exists() else sys.executable
    else:
        executable = sys.executable

    cmd = [executable, "-m", "seraphim.learning.daemon"]
    kwargs: dict = {"stdout": log_file, "stderr": log_file}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **kwargs)

    # Wait up to 3s for PID file to confirm daemon started
    for _ in range(15):
        time.sleep(0.2)
        if PID_FILE.exists():
            break

    console.print(f"[green]✓[/green] Daemon started (PID {proc.pid})")
    console.print(f"  Interval : [cyan]{interval}h[/cyan]  Agents: [cyan]{agents}[/cyan]"
                  + ("  [yellow]GRPO[/yellow]" if grpo else "")
                  + ("  [magenta]finetune[/magenta]" if finetune else ""))
    console.print(f"  Log      : [dim]{LOG_FILE}[/dim]")
    console.print(f"  Status   : [bold]seraphim learn daemon status[/bold]")
    console.print(f"  Stop     : [bold]seraphim learn daemon stop[/bold]")


@daemon_app.command("stop")
def daemon_stop():
    """Stop the running learning daemon."""
    from seraphim.learning.daemon import PID_FILE

    if not PID_FILE.exists():
        console.print("[yellow]⚠[/yellow]  No daemon running (no PID file).")
        return
    pid_str = PID_FILE.read_text().strip()
    try:
        pid = int(pid_str)
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
        PID_FILE.unlink(missing_ok=True)
        console.print(f"[green]✓[/green] Daemon (PID {pid}) stopped.")
    except (ValueError, ProcessLookupError):
        PID_FILE.unlink(missing_ok=True)
        console.print(f"[yellow]⚠[/yellow]  PID {pid_str} not found — PID file removed.")
    except PermissionError:
        console.print(f"[red]✗[/red] Permission denied killing PID {pid_str}.")


@daemon_app.command("restart")
def daemon_restart(
    interval: float = typer.Option(6.0, "--interval", "-i"),
    agents: str = typer.Option("react,chat", "--agents", "-a"),
    min_quality: float = typer.Option(0.6, "--quality", "-q"),
    min_new_traces: int = typer.Option(3, "--min-traces"),
    grpo: bool = typer.Option(False, "--grpo", "-g"),
    grpo_generations: int = typer.Option(4, "--grpo-g"),
    grpo_max_prompts: int = typer.Option(30, "--grpo-n"),
    finetune: bool = typer.Option(False, "--finetune", "-f"),
):
    """Restart the learning daemon (stop then start)."""
    from seraphim.learning.daemon import PID_FILE, CONFIG_FILE, is_alive

    if PID_FILE.exists():
        pid_str = PID_FILE.read_text().strip()
        try:
            pid = int(pid_str)
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            else:
                os.kill(pid, signal.SIGTERM)
            PID_FILE.unlink(missing_ok=True)
            console.print(f"[dim]Stopped PID {pid}.[/dim]")
        except Exception:
            PID_FILE.unlink(missing_ok=True)

    # Re-use start logic
    daemon_start(
        interval=interval, agents=agents, min_quality=min_quality,
        min_new_traces=min_new_traces, grpo=grpo, grpo_generations=grpo_generations,
        grpo_max_prompts=grpo_max_prompts, finetune=finetune,
    )


@daemon_app.command("status")
def daemon_status():
    """Show learning daemon status."""
    from seraphim.learning.daemon import PID_FILE, LOG_FILE, STATE_FILE, is_alive, read_state

    state = read_state()
    pid_alive = False

    if state.get("pid"):
        pid_alive = is_alive(int(state["pid"]))
    elif PID_FILE.exists():
        try:
            pid_alive = is_alive(int(PID_FILE.read_text().strip()))
        except ValueError:
            pass

    if not state:
        console.print("[yellow]⚠[/yellow]  Daemon never started on this machine.")
        return

    status_val = state.get("status", "unknown")
    if status_val == "stopped" or not pid_alive:
        status_str = "[red]● stopped[/red]"
    elif status_val == "training":
        status_str = "[yellow]● training[/yellow]"
    elif status_val == "sleeping":
        status_str = "[green]● running[/green]"
    else:
        status_str = f"[dim]{status_val}[/dim]"

    console.print(f"\n[bold]Learning daemon[/bold]  {status_str}")
    if state.get("pid"):
        console.print(f"  PID       : [cyan]{state['pid']}[/cyan]"
                      + ("  [dim](alive)[/dim]" if pid_alive else "  [red](dead)[/red]"))
    if state.get("started_at"):
        console.print(f"  Started   : [dim]{state['started_at']}[/dim]")
    if state.get("run_count") is not None:
        console.print(f"  Runs done : [yellow]{state['run_count']}[/yellow]")
    if state.get("next_run") and status_val == "sleeping":
        console.print(f"  Next run  : [cyan]{state['next_run']}[/cyan]")
    if state.get("last_run"):
        console.print(f"  Last run  : [dim]{state['last_run']}[/dim]")

    lr = state.get("last_result")
    if lr and lr.get("at"):
        if lr.get("error"):
            console.print(f"  Last result: [red]error — {lr['error'][:60]}[/red]")
        else:
            console.print(
                f"  Last result: mined=[yellow]{lr.get('mined', 0)}[/yellow]  "
                f"accepted=[green]{lr.get('accepted', 0)}[/green]  "
                f"rejected=[red]{lr.get('rejected', 0)}[/red]"
                + (f"  grpo_pairs=[cyan]{lr['grpo_pairs']}[/cyan]" if lr.get("grpo_pairs") else "")
            )

    cfg = state.get("config", {})
    if cfg:
        auto = "[green]auto-start ON[/green]" if cfg.get("auto_start") else "[dim]auto-start off[/dim]"
        console.print(
            f"\n  [dim]interval={cfg.get('interval_hours', '?')}h  "
            f"agents={cfg.get('agents', '?')}  "
            f"grpo={cfg.get('run_grpo', False)}  "
            f"finetune={cfg.get('run_finetune', False)}[/dim]  {auto}"
        )
    console.print(f"  Log: [dim]{LOG_FILE}[/dim]\n")


@daemon_app.command("logs")
def daemon_logs(
    lines: int = typer.Option(50, "--lines", "-n", help="Number of recent log lines to show"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output (like tail -f)"),
):
    """Show recent learning daemon log output."""
    from seraphim.learning.daemon import LOG_FILE

    if not LOG_FILE.exists():
        console.print("[dim]No log file yet. Start the daemon first.[/dim]")
        return

    if follow:
        console.print(f"[dim]Following {LOG_FILE} — Ctrl+C to stop[/dim]\n")
        try:
            with open(LOG_FILE) as f:
                f.seek(0, 2)  # seek to end
                while True:
                    line = f.readline()
                    if line:
                        console.print(line.rstrip())
                    else:
                        time.sleep(0.5)
        except KeyboardInterrupt:
            return

    # Show last N lines
    try:
        all_lines = LOG_FILE.read_text(errors="replace").splitlines()
        recent = all_lines[-lines:]
        console.print(f"[dim]Last {len(recent)} lines of {LOG_FILE}:[/dim]\n")
        for line in recent:
            if "ERROR" in line or "CRITICAL" in line:
                console.print(f"[red]{line}[/red]")
            elif "WARNING" in line:
                console.print(f"[yellow]{line}[/yellow]")
            elif "Run #" in line and "done" in line:
                console.print(f"[green]{line}[/green]")
            else:
                console.print(f"[dim]{line}[/dim]")
    except Exception as e:
        console.print(f"[red]✗[/red] Could not read log: {e}")


@app.command("routing")
def routing_cmd(
    reset: bool = typer.Option(False, "--reset", help="Delete all learned routing stats"),
    min_samples: int = typer.Option(1, "--min-samples", "-n", help="Only show entries with ≥ N samples"),
):
    """Show learned routing statistics (which agent wins per query class)."""
    async def _run():
        from seraphim.agents.learned_router import get_routing_stats, _DB_PATH, _ensure_table
        import aiosqlite

        if reset:
            await _ensure_table()
            async with aiosqlite.connect(_DB_PATH) as db:
                await db.execute("DELETE FROM routing_stats")
                await db.commit()
            console.print("[green]✓[/green] Routing stats cleared.")
            return

        stats = await get_routing_stats()
        if not stats:
            console.print("[dim]No routing stats yet. Stats accumulate as the system handles queries.[/dim]")
            return

        filtered = [s for s in stats if s["sample_count"] >= min_samples]
        if not filtered:
            console.print(f"[dim]No entries with ≥ {min_samples} samples yet.[/dim]")
            return

        t = Table(show_header=True, title="Learned Routing Stats")
        t.add_column("Query Class", style="cyan")
        t.add_column("Agent")
        t.add_column("Samples", justify="right")
        t.add_column("Mean Score", justify="right")
        t.add_column("Mean Latency", justify="right")
        t.add_column("Status")

        current_class = None
        for s in filtered:
            is_best = current_class != s["query_class"]
            current_class = s["query_class"]
            score_color = "green" if s["mean_score"] >= 0.7 else ("yellow" if s["mean_score"] >= 0.5 else "red")
            t.add_row(
                s["query_class"] if is_best else "",
                f"[bold]{s['agent']}[/bold]" if is_best else s["agent"],
                str(s["sample_count"]),
                f"[{score_color}]{s['mean_score']:.3f}[/{score_color}]",
                f"{int(s['mean_latency_ms'])}ms" if s["mean_latency_ms"] else "—",
                "[green]★ best[/green]" if is_best else "",
            )
        console.print(t)
        console.print(f"\n[dim]Learned router activates at ≥5 samples with ≥0.05 score advantage.[/dim]")

    asyncio.run(_run())


@app.command("traces")
def traces_cmd(
    agent: str = typer.Option("", "--agent", "-a"),
    limit: int = typer.Option(20, "--limit", "-n"),
):
    """List recent traces."""
    async def _run():
        from seraphim.learning.trace_store import load_traces
        traces = await load_traces(agent=agent or None, limit=limit)
        if not traces:
            console.print("[dim]No traces yet.[/dim]")
            return
        t = Table(show_header=True)
        t.add_column("ID", style="dim", max_width=12)
        t.add_column("Agent")
        t.add_column("Query", max_width=40)
        t.add_column("Steps")
        t.add_column("OK")
        t.add_column("Feedback")
        for tr in traces:
            t.add_row(
                tr.id[:8],
                tr.agent,
                tr.query[:40],
                str(len(tr.steps)),
                "✓" if tr.success else "✗",
                f"{tr.feedback:.2f}" if tr.feedback >= 0 else "—",
            )
        console.print(t)
    asyncio.run(_run())

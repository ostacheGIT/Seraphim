"""CLI sub-commands for managing the vLLM GPU inference server."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

app = typer.Typer(name="vllm", help="Manage the vLLM GPU inference server.")
console = Console()

_PID_FILE = Path.home() / ".seraphim" / "vllm.pid"


def _wsl_available() -> bool:
    """Return True if WSL2 can actually execute commands (not just installed)."""
    try:
        result = subprocess.run(
            ["wsl", "--", "echo", "ok"],
            capture_output=True,
            timeout=8,
        )
        return result.returncode == 0 and b"ok" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _build_vllm_cmd(model: str, port: int, gpu_util: float, max_len: int,
                    dtype: str, tensor_parallel: int, quantization: str | None) -> list[str]:
    """Build the vllm serve command, prepending 'wsl --' on Windows."""
    vllm_args = [
        "vllm", "serve", model,
        "--gpu-memory-utilization", str(gpu_util),
        "--max-model-len", str(max_len),
        "--port", str(port),
        "--dtype", dtype,
        "--host", "0.0.0.0",
    ]
    if tensor_parallel > 1:
        vllm_args += ["--tensor-parallel-size", str(tensor_parallel)]
    if quantization:
        vllm_args += ["--quantization", quantization]

    if sys.platform == "win32":
        return ["wsl", "--"] + vllm_args
    return vllm_args


@app.command("serve")
def vllm_serve(
    model: Optional[str] = typer.Option(None, "--model", "-m", help="HuggingFace model ID"),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="Server port (default from config)"),
    gpu_memory_utilization: Optional[float] = typer.Option(
        None, "--gpu-memory-utilization", "-g",
        help="Fraction of VRAM to use, e.g. 0.85 for 4 GB cards",
    ),
    max_model_len: Optional[int] = typer.Option(None, "--max-model-len", help="Max context length in tokens"),
    background: bool = typer.Option(False, "--background", "-b", help="Detach and run in background"),
    tensor_parallel: int = typer.Option(
        1, "--tensor-parallel-size", "-tp", help="Number of GPUs for tensor parallelism",
    ),
    dtype: str = typer.Option("auto", "--dtype", help="Weight dtype: auto | float16 | bfloat16"),
    quantization: Optional[str] = typer.Option(
        None, "--quantization", "-q", help="Quantization: awq | gptq | squeezellm | None",
    ),
):
    """Start the vLLM OpenAI-compatible server with GPU acceleration."""
    from seraphim.settings import settings

    _model = model or settings.engine.model
    _port = port or settings.engine.vllm_port
    _gpu_util = gpu_memory_utilization if gpu_memory_utilization is not None else settings.engine.vllm_gpu_memory_utilization
    _max_len = max_model_len or settings.engine.vllm_max_model_len

    if sys.platform == "win32" and not _wsl_available():
        console.print("[red]✗ vLLM requires WSL2 on Windows (native CUDA extensions not supported).[/red]")
        console.print()
        console.print("[bold]Setup WSL2 + vLLM:[/bold]")
        console.print("  1. Enable WSL2:  [bold]wsl --install[/bold]  (restart required)")
        console.print("  2. In WSL2 terminal:")
        console.print("       pip install vllm")
        console.print(f"       vllm serve {_model} \\")
        console.print(f"           --gpu-memory-utilization {_gpu_util} \\")
        console.print(f"           --max-model-len {_max_len} \\")
        console.print(f"           --port {_port} --host 0.0.0.0")
        console.print("  3. Seraphim (Windows side) connects via http://localhost:8000 as usual.")
        raise typer.Exit(1)

    cmd = _build_vllm_cmd(_model, _port, _gpu_util, _max_len, dtype, tensor_parallel, quantization)

    via = "WSL2" if sys.platform == "win32" else "native"
    console.print(f"[bold cyan]Starting vLLM server[/bold cyan] [dim]({via})[/dim]")
    console.print(f"  Model  : [bold]{_model}[/bold]")
    console.print(f"  Port   : {_port}")
    console.print(f"  GPU mem: {_gpu_util * 100:.0f}%")
    console.print(f"  Context: {_max_len} tokens")
    console.print(f"  dtype  : {dtype}")
    if quantization:
        console.print(f"  Quant  : {quantization}")
    if tensor_parallel > 1:
        console.print(f"  TP size: {tensor_parallel} GPUs")

    if background:
        _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        popen_kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            )
        else:
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
        except FileNotFoundError:
            console.print("[red]✗ 'vllm' not found. Install: pip install vllm[/red]")
            raise typer.Exit(1)

        _PID_FILE.write_text(str(proc.pid))
        console.print(f"\n[green]✓[/green] vLLM started (PID {proc.pid})")
        console.print(f"  API  : http://localhost:{_port}/v1")
        console.print(f"  Stop : [bold]seraphim vllm stop[/bold]")
        console.print(f"  Check: [bold]seraphim vllm status[/bold]")
    else:
        console.print(f"\n  [dim]API: http://localhost:{_port}/v1  |  Ctrl-C to stop[/dim]\n")
        try:
            subprocess.run(cmd, check=False)
        except FileNotFoundError:
            console.print("[red]✗ 'vllm' not found. Install: pip install vllm[/red]")
            raise typer.Exit(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]vLLM server stopped.[/yellow]")


@app.command("stop")
def vllm_stop():
    """Stop the background vLLM server."""
    if not _PID_FILE.exists():
        console.print("[yellow]No background vLLM PID file found.[/yellow]")
        raise typer.Exit(1)

    pid = int(_PID_FILE.read_text().strip())
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0 and "not found" in result.stderr.lower():
                raise ProcessLookupError
        else:
            import signal
            os.kill(pid, signal.SIGTERM)

        _PID_FILE.unlink(missing_ok=True)
        console.print(f"[green]✓[/green] vLLM server (PID {pid}) stopped.")
    except ProcessLookupError:
        console.print(f"[yellow]Process {pid} already gone.[/yellow]")
        _PID_FILE.unlink(missing_ok=True)
    except Exception as e:
        console.print(f"[red]Error stopping vLLM: {e}[/red]")
        raise typer.Exit(1)


@app.command("status")
def vllm_status():
    """Check vLLM server health and live GPU stats."""
    from seraphim.settings import settings

    async def _check() -> None:
        from seraphim.engine.vllm import VLLMEngine
        from seraphim.engine.metrics import get_gpu_snapshot

        base_url = f"http://localhost:{settings.engine.vllm_port}"
        vllm = VLLMEngine(model=settings.engine.model, base_url=base_url)

        ok = await vllm.health_check()
        dot = "[green]●[/green]" if ok else "[red]●[/red]"
        label = "Running" if ok else "Not running"
        console.print(f"\n[bold]vLLM[/bold] {dot} {label}  ({base_url})")

        if ok:
            try:
                models = await vllm.list_models()
                console.print(f"  Loaded: {', '.join(models) or '(none)'}")
            except Exception:
                pass

        gpu = get_gpu_snapshot()
        if gpu:
            filled = min(20, int(gpu.vram_used_pct / 5))
            bar = "█" * filled + "░" * (20 - filled)
            console.print(f"\n[bold]GPU[/bold]  {gpu.gpu_name}")
            console.print(f"  Util : {gpu.gpu_util_pct:.0f}%")
            console.print(f"  VRAM : [{bar}] {gpu.vram_used_mb:.0f} / {gpu.vram_total_mb:.0f} MB  ({gpu.vram_used_pct:.1f}%)")
        else:
            console.print("\n[dim]GPU: not detected (install pynvml or ensure nvidia-smi is on PATH)[/dim]")

        if _PID_FILE.exists():
            pid = _PID_FILE.read_text().strip()
            console.print(f"\n  [dim]Background PID: {pid}[/dim]")

    asyncio.run(_check())


@app.command("config")
def vllm_config_show():
    """Show current vLLM settings from config."""
    from seraphim.settings import settings
    s = settings.engine
    console.print("\n[bold]vLLM config[/bold]")
    console.print(f"  provider               : {s.provider}")
    console.print(f"  model                  : {s.model}")
    console.print(f"  base_url               : {s.base_url}")
    console.print(f"  vllm_port              : {s.vllm_port}")
    console.print(f"  vllm_gpu_memory_util   : {s.vllm_gpu_memory_utilization}")
    console.print(f"  vllm_max_model_len     : {s.vllm_max_model_len}")
    console.print(f"  temperature            : {s.temperature}")
    console.print()
    console.print("[dim]Edit configs/seraphim/config.yaml to change provider/model.[/dim]")
    console.print("[dim]To switch to vLLM: set engine.provider=vllm and engine.model=<hf-model-id>[/dim]")

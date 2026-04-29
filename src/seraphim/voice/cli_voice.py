# src/seraphim/voice/cli_voice.py
"""
Commande `seraphim listen` — écoute vocale continue.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()


def listen_command(
        agent: str = typer.Option("chat", "--agent", "-a", help="Agent : chat, coder, researcher"),
        model: str = typer.Option("small", "--whisper-model", "-w",
                                  help="Modèle Whisper : tiny | base | small | medium | large-v3"),
        language: Optional[str] = typer.Option("fr", "--language", "-l",
                                               help="Langue forcée (ex: fr, en). Défaut: auto-détection"),
        silence: float = typer.Option(1.5, "--silence", "-s",
                                      help="Secondes de silence avant coupure"),
        threshold: float = typer.Option(0.01, "--threshold", "-t",
                                        help="Seuil énergie micro (0.0–1.0)"),
        session: Optional[str] = typer.Option(None, "--session", help="Session ID pour la mémoire"),
        no_memory: bool = typer.Option(False, "--no-memory", help="Désactive la mémoire"),
        stream: bool = typer.Option(True, "--stream/--no-stream", help="Stream la réponse"),
):
    """🎙️ Écoute vocale continue — parlez, Seraphim vous répond. Ctrl+C pour quitter."""
    try:
        from seraphim.voice.listener import VoiceListener
    except ImportError:
        console.print(
            "[bold red]Dépendances voix manquantes.[/bold red]\n"
            "Lance : [bold]uv pip install '.[voice]' sounddevice[/bold]"
        )
        raise typer.Exit(1)

    listener = VoiceListener(
        model_size=model,
        language=language,
        silence_duration=silence,
        energy_threshold=threshold,
    )

    sess = session or str(uuid.uuid4())[:8]
    console.print(Panel(
        Text.from_markup(
            f"🎙️ [bold cyan]Seraphim écoute[/bold cyan] — agent: [yellow]{agent}[/yellow] session: [dim]{sess}[/dim]\n"
            f"Whisper: [green]{model}[/green] | Langue: [green]{language or 'auto'}[/green]\n\n"
            "[dim]Parlez… [bold]Ctrl+C[/bold] pour quitter.[/dim]"
        ),
        title="Mode vocal",
        border_style="cyan",
    ))

    async def _respond(query: str):
        from seraphim.agents.base import get_agent
        from seraphim.agents.core import AgentContext
        from seraphim.engine.ollama import engine as eng
        from seraphim.memory.store import init_db, load_history, save_message
        from seraphim.voice.speaker import speak_async, synthesize_to_bytes
        from concurrent.futures import ThreadPoolExecutor

        ag = get_agent(agent)
        ctx = AgentContext()
        ctx.add_system(ag.system_prompt)

        if not no_memory:
            await init_db()
            history = await load_history(sess)
            for msg in history:
                ctx.messages.append(msg)

        ctx.add_user(query)

        console.print(f"[bold green]Seraphim ({agent}) ›[/bold green] ", end="")
        full_response = ""
        sentence_buf  = ""
        tts_executor  = ThreadPoolExecutor(max_workers=2)
        tts_futures   = []

        async for token in eng.stream_chat(ctx.messages):
            console.print(token, end="", highlight=False)
            full_response += token
            sentence_buf  += token

            # Dès qu'une phrase est complète, lance la synthèse en parallèle
            if re.search(r'[.!?]\s', sentence_buf):
                parts = re.split(r'(?<=[.!?])\s', sentence_buf)
                for s in parts[:-1]:
                    if s.strip():
                        tts_futures.append(tts_executor.submit(synthesize_to_bytes, s))
                sentence_buf = parts[-1]

        # Synthétise le reste
        if sentence_buf.strip():
            tts_futures.append(tts_executor.submit(synthesize_to_bytes, sentence_buf))

        console.print()

        # Joue les phrases dans l'ordre (déjà pré-synthétisées)
        import io, wave
        import numpy as np
        import sounddevice as sd

        for future in tts_futures:
            wav_bytes = future.result()
            buf = io.BytesIO(wav_bytes)
            with wave.open(buf, "rb") as wf:
                frames      = wf.readframes(wf.getnframes())
                sample_rate = wf.getframerate()
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            sd.play(audio, samplerate=sample_rate)
            sd.wait()

        if not no_memory:
            await save_message(sess, "user", query, agent)
            await save_message(sess, "assistant", full_response, agent)

    STOP_KEYWORDS = [
        "arrête d'écouter", "arrête de m'écouter", "arrêtes d'écouter",
        "stop écoute", "stop l'écoute", "arrête l'écoute",
        "seraphim stop", "au revoir seraphim", "bye seraphim",
        "stop listening", "stop",
    ]

    while True:
        try:
            console.print("\n[dim cyan]⏳ En écoute…[/dim cyan]", end="")
            text = listener.listen_and_transcribe()
            if not text:
                console.print(" [dim](silence, on recommence)[/dim]")
                continue

            console.print(f"\n[bold cyan]Vous ›[/bold cyan] {text}")

            text_lower = text.lower().strip()
            if any(kw in text_lower for kw in STOP_KEYWORDS):
                console.print("[bold yellow]Seraphim ›[/bold yellow] Bien, j'arrête de vous écouter. À bientôt 👋")
                break

            asyncio.run(_respond(text))
        except KeyboardInterrupt:
            console.print("\n\n[dim]Au revoir 👋[/dim]")
            break
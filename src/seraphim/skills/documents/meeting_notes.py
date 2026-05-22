"""MeetingNotesSkill — transcribe audio/video + expose transcript for the agent."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

from seraphim.skills.base import BaseSkill, SkillResult

_SUPPORTED = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm", ".mp4", ".mkv", ".avi"}
_MAX_TRANSCRIPT = 15_000


class MeetingNotesSkill(BaseSkill):
    name = "meeting_notes"
    description = (
        "Transcribe an audio or video recording using Whisper and return the full transcript. "
        "The agent then generates a summary and extracts action items from the transcript. "
        "Supported formats: mp3, wav, m4a, ogg, flac, webm, mp4, mkv."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the audio or video file",
            },
            "language": {
                "type": "string",
                "description": "Language code, e.g. 'fr' or 'en'. Default: auto-detect.",
                "default": "",
            },
            "model": {
                "type": "string",
                "description": "Whisper model size: tiny, base, small, medium (default: base)",
                "default": "base",
            },
        },
        "required": ["path"],
    }

    async def run(
        self,
        path: str,
        language: str = "",
        model: str = "base",
        **kwargs,
    ) -> SkillResult:
        p = Path(path).expanduser()
        if not p.exists():
            return SkillResult(success=False, output="", error=f"Fichier introuvable : {path}")
        if p.suffix.lower() not in _SUPPORTED:
            return SkillResult(
                success=False,
                output="",
                error=f"Format non supporté : {p.suffix}. Supportés : {', '.join(sorted(_SUPPORTED))}",
            )

        loop = asyncio.get_running_loop()
        transcript, error = await loop.run_in_executor(
            None, _transcribe, str(p), language, model
        )
        if error:
            return SkillResult(success=False, output="", error=error)

        truncated = len(transcript) > _MAX_TRANSCRIPT
        display = transcript[:_MAX_TRANSCRIPT]
        if truncated:
            display += "\n\n[… transcription tronquée]"

        output = (
            f"[Transcription : {p.name} | Whisper {model}]\n\n"
            f"{display}\n\n"
            "---\n"
            "Transcription terminée. Génère maintenant :\n"
            "1. Un résumé en 5 points clés\n"
            "2. La liste des action items (qui fait quoi, deadline si mentionnée)\n"
            "3. Les décisions prises"
        )
        return SkillResult(success=True, output=output)


# ── Transcription backends (tried in order) ───────────────────────────────────

def _transcribe(path: str, language: str, model_name: str) -> tuple[str, str]:
    """Returns (transcript_text, error_message). One of them will be empty."""

    # 1 — faster-whisper (best speed/quality ratio, GPU-aware)
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(model_name, device="auto", compute_type="auto")
        segments, _ = model.transcribe(path, language=language or None, beam_size=5)
        text = " ".join(seg.text.strip() for seg in segments)
        return text.strip(), ""
    except ImportError:
        pass
    except Exception as e:
        return "", f"faster-whisper erreur : {e}"

    # 2 — openai-whisper
    try:
        import whisper
        model = whisper.load_model(model_name)
        opts: dict = {}
        if language:
            opts["language"] = language
        result = model.transcribe(path, **opts)
        return (result.get("text") or "").strip(), ""
    except ImportError:
        pass
    except Exception as e:
        return "", f"openai-whisper erreur : {e}"

    # 3 — whisper CLI (installed via pip or standalone binary)
    try:
        tmp = Path(tempfile.gettempdir())
        args = [
            "whisper", path,
            "--model", model_name,
            "--output_format", "txt",
            "--output_dir", str(tmp),
        ]
        if language:
            args += ["--language", language]
        proc = subprocess.run(args, capture_output=True, text=True, timeout=300)
        txt_path = tmp / (Path(path).stem + ".txt")
        if proc.returncode == 0 and txt_path.exists():
            return txt_path.read_text(encoding="utf-8").strip(), ""
        return "", f"whisper CLI erreur : {proc.stderr[:300]}"
    except FileNotFoundError:
        pass
    except Exception as e:
        return "", f"whisper CLI erreur : {e}"

    return "", (
        "Aucune bibliothèque Whisper disponible. Installe l'une de :\n"
        "  pip install faster-whisper   # recommandé (rapide, GPU)\n"
        "  pip install openai-whisper   # alternative officielle"
    )

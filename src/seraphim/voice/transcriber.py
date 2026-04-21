# src/seraphim/voice/transcriber.py
"""
Transcription audio -> texte via faster-whisper (local, offline).
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

import numpy as np


class Transcriber:
    """Transcrit un tableau numpy ou un fichier WAV en texte avec faster-whisper."""

    def __init__(self, model_size: str = "base", language: Optional[str] = None, device: str = "cpu"):
        """
        Args:
            model_size: taille du modèle Whisper ("tiny", "base", "small", "medium", "large-v3").
                        "base" est un bon compromis vitesse/qualité pour du chat vocal.
            language:   forcer la langue ("fr", "en", …) ou None pour auto-détection.
            device:     "cpu" ou "cuda" si GPU disponible.
        """
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ImportError(
                "faster-whisper n'est pas installé. Lance : uv pip install 'seraphim[voice]'"
            ) from exc

        self._model = WhisperModel(model_size, device=device, compute_type="int8")
        self._language = language

    def transcribe_array(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        """
        Transcrit un tableau numpy float32 mono.

        Args:
            audio:       signal audio en float32 normalisé [-1, 1].
            sample_rate: fréquence d'échantillonnage (Whisper attend 16 kHz).
        Returns:
            Texte transcrit (peut être vide si silence).
        """
        # faster-whisper accepte directement un ndarray float32 16 kHz
        segments, _ = self._model.transcribe(
            audio,
            language=self._language,
            beam_size=5,
            vad_filter=True,          # filtre les silences via WebRTC VAD intégré
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    def transcribe_file(self, path: str | Path) -> str:
        """Transcrit un fichier audio (WAV, MP3, …)."""
        segments, _ = self._model.transcribe(
            str(path),
            language=self._language,
            beam_size=5,
            vad_filter=True,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
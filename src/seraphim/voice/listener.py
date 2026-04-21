# src/seraphim/voice/listener.py
"""
Capture microphone avec détection automatique de fin de parole.

Utilise sounddevice (PortAudio) pour lire le micro en temps réel.
La détection de fin de parole repose sur l'énergie RMS du signal :
  - quand l'utilisateur parle, l'énergie dépasse un seuil
  - quand il s'arrête pendant `silence_duration` secondes → on coupe

Usage minimal :
    listener = VoiceListener()
    audio = listener.listen()          # bloque jusqu'à fin de parole
    text  = listener.transcriber.transcribe_array(audio)
"""
from __future__ import annotations

import queue
import time
from typing import Optional

import numpy as np

from .transcriber import Transcriber


SAMPLE_RATE = 16_000          # Hz  (Whisper veut 16 kHz)
CHANNELS    = 1               # mono
BLOCK_SIZE  = 1_024           # frames par callback (~64 ms)


def _rms(block: np.ndarray) -> float:
    return float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))


class VoiceListener:
    """
    Écoute en continu le microphone et retourne un segment audio
    dès que l'utilisateur a fini de parler.

    Args:
        energy_threshold:  seuil RMS pour détecter la parole (0.0–1.0 pour float32).
                           Ajuste si le micro est trop sensible ou pas assez.
        silence_duration:  secondes de silence avant de couper l'enregistrement.
        max_duration:      durée maximale d'un énoncé en secondes.
        model_size:        modèle Whisper à utiliser.
        language:          langue forcée (None = auto).
        device:            "cpu" ou "cuda".
    """

    def __init__(
            self,
            energy_threshold: float = 0.01,
            silence_duration: float = 1.5,
            max_duration: float = 30.0,
            model_size: str = "base",
            language: Optional[str] = None,
            device: str = "cpu",
    ):
        self.energy_threshold = energy_threshold
        self.silence_duration = silence_duration
        self.max_duration = max_duration
        self.transcriber = Transcriber(model_size=model_size, language=language, device=device)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def listen(self) -> np.ndarray:
        """
        Bloque jusqu'à ce qu'un énoncé complet soit capturé.
        Retourne un tableau numpy float32 16 kHz mono.
        """
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise ImportError(
                "sounddevice n'est pas installé. Lance : uv pip install sounddevice"
            ) from exc

        audio_queue: queue.Queue[np.ndarray] = queue.Queue()

        def callback(indata, frames, time_info, status):
            audio_queue.put(indata.copy())

        frames_per_second = SAMPLE_RATE / BLOCK_SIZE
        silence_blocks    = int(self.silence_duration * frames_per_second)
        max_blocks        = int(self.max_duration     * frames_per_second)

        recorded: list[np.ndarray] = []
        speech_started  = False
        silence_counter = 0

        with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                blocksize=BLOCK_SIZE,
                callback=callback,
        ):
            while True:
                block = audio_queue.get().flatten()

                if _rms(block) >= self.energy_threshold:
                    speech_started  = True
                    silence_counter = 0
                elif speech_started:
                    silence_counter += 1

                if speech_started:
                    recorded.append(block)

                if speech_started and silence_counter >= silence_blocks:
                    break
                if len(recorded) >= max_blocks:
                    break

        return np.concatenate(recorded) if recorded else np.array([], dtype=np.float32)

    def listen_and_transcribe(self) -> str:
        """
        Raccourci : capture + transcription en une seule ligne.
        Retourne le texte de l'énoncé (chaîne vide si silence/inaudible).
        """
        audio = self.listen()
        if len(audio) == 0:
            return ""
        return self.transcriber.transcribe_array(audio, sample_rate=SAMPLE_RATE)
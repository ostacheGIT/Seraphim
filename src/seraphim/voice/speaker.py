# src/seraphim/voice/speaker.py
"""
Synthèse vocale via Piper TTS - Voix Tom (fr_FR), locale, ~200ms.
Effets : pitch grave + reverb légère.
"""

import io
import os
import re
import wave
import hashlib
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import sounddevice as sd
from scipy import signal
from piper import PiperVoice

VOICE_MODEL = Path(__file__).parent.parent.parent.parent / "voices" / "fr_FR-tom-medium.onnx"
PITCH       = 1.0   # < 1.0 = plus grave
REVERB_MIX  = 0.15
REVERB_MS   = 0.025

_CACHE_DIR = Path.home() / ".seraphim" / "tts_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_voice: PiperVoice | None = None
_lock = threading.Lock()
_tts_executor = ThreadPoolExecutor(max_workers=2)


def _get_voice() -> PiperVoice:
    global _voice
    if _voice is None:
        with _lock:
            if _voice is None:
                _voice = PiperVoice.load(str(VOICE_MODEL))
    return _voice


def _apply_effects(audio: np.ndarray, sr: int) -> np.ndarray:
    # Pitch shift
    resampled = signal.resample(audio, int(len(audio) / PITCH))
    # Reverb
    delay = int(sr * REVERB_MS)
    reverb = np.zeros_like(resampled)
    reverb[delay:] = resampled[:-delay] * REVERB_MIX
    return np.clip(resampled + reverb, -1.0, 1.0)


def synthesize_to_bytes(text: str) -> bytes:
    key = hashlib.md5(text.strip().encode()).hexdigest()
    cache_file = _CACHE_DIR / f"{key}.wav"
    if cache_file.exists():
        return cache_file.read_bytes()

    voice = _get_voice()
    sr = voice.config.sample_rate
    tmp = tempfile.mktemp(suffix=".wav")
    try:
        with wave.open(tmp, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            voice.synthesize_wav(text, wf)
        with wave.open(tmp, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    audio = _apply_effects(audio, sr)
    pcm   = (audio * 32767).astype(np.int16).tobytes()

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm)
    wav = buf.getvalue()
    cache_file.write_bytes(wav)
    return wav


def synthesize_stream(text: str):
    """Générateur de chunks PCM int16 — pour StreamingResponse FastAPI."""
    wav_bytes = synthesize_to_bytes(text)
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, "rb") as wf:
        yield wf.readframes(wf.getnframes())


def _play_wav(wav_bytes: bytes) -> None:
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, "rb") as wf:
        frames = wf.readframes(wf.getnframes())
        sr     = wf.getframerate()
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    sd.play(audio, samplerate=sr)
    sd.wait()


def speak(text: str) -> None:
    sentences = [s for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s.strip()]
    if len(sentences) <= 1:
        _play_wav(synthesize_to_bytes(text))
        return
    futures = [_tts_executor.submit(synthesize_to_bytes, s) for s in sentences]
    for f in futures:
        _play_wav(f.result())


def speak_async(text: str) -> None:
    threading.Thread(target=speak, args=(text,), daemon=True).start()
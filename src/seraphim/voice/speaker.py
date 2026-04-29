# src/seraphim/voice/speaker.py
import io
import wave
import threading
import numpy as np
import sounddevice as sd
from piper.voice import PiperVoice
from huggingface_hub import hf_hub_download

JARVIS_REPO = "jgkawell/jarvis"
JARVIS_MODEL = "en/en_GB/jarvis/medium/jarvis-medium.onnx"
JARVIS_CONFIG = "en/en_GB/jarvis/medium/jarvis-medium.onnx.json"

_voice_instance: PiperVoice | None = None
_lock = threading.Lock()


def _get_voice() -> PiperVoice:
    global _voice_instance
    if _voice_instance is None:
        with _lock:
            if _voice_instance is None:
                model_path = hf_hub_download(JARVIS_REPO, JARVIS_MODEL)
                config_path = hf_hub_download(JARVIS_REPO, JARVIS_CONFIG)
                _voice_instance = PiperVoice.load(model_path, config_path=config_path)
    return _voice_instance


def _make_wav_buffer(voice: PiperVoice, text: str) -> io.BytesIO:
    """Synthétise le texte et retourne un BytesIO contenant le WAV complet."""
    audio_buffer = io.BytesIO()
    with wave.open(audio_buffer, "wb") as wav_file:
        # Configurer le WAV AVANT de passer à synthesize()
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # int16 = 2 bytes
        wav_file.setframerate(voice.config.sample_rate)
        voice.synthesize(text, wav_file)
    audio_buffer.seek(0)
    return audio_buffer


def synthesize_to_bytes(text: str) -> bytes:
    """Retourne les bytes WAV complets prêts à streamer vers le frontend."""
    voice = _get_voice()
    buf = _make_wav_buffer(voice, text)
    return buf.read()


def speak(text: str) -> None:
    """Synthétise et joue le texte directement (bloquant)."""
    voice = _get_voice()
    buf = _make_wav_buffer(voice, text)
    with wave.open(buf, "rb") as wav_file:
        frames = wav_file.readframes(wav_file.getnframes())
        sample_rate = wav_file.getframerate()
    audio_array = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    sd.play(audio_array, samplerate=sample_rate)
    sd.wait()


def speak_async(text: str) -> None:
    """Synthétise en arrière-plan (non bloquant)."""
    threading.Thread(target=speak, args=(text,), daemon=True).start()
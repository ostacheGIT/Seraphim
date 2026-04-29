# src/seraphim/voice/speaker.py
"""
Synthèse vocale via Microsoft Edge TTS - Voix Remy (fr-FR).
"""

import io
import wave
import asyncio
import threading
import tempfile
import numpy as np
import sounddevice as sd
import edge_tts
import miniaudio

VOICE = "fr-FR-RemyMultilingualNeural"
SAMPLE_RATE = 24000


async def _synthesize_async(text: str) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp_path = f.name
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(tmp_path)
    decoded = miniaudio.mp3_read_file_f32(tmp_path)
    audio = np.array(decoded.samples, dtype=np.float32)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(decoded.nchannels)
        wf.setsampwidth(2)
        wf.setframerate(decoded.sample_rate)
        wf.writeframes((audio * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def synthesize_to_bytes(text: str) -> bytes:
    return asyncio.run(_synthesize_async(text))


def synthesize_stream(text: str):
    wav_bytes = synthesize_to_bytes(text)
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, "rb") as wf:
        yield wf.readframes(wf.getnframes())


def speak(text: str) -> None:
    wav_bytes = synthesize_to_bytes(text)
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, "rb") as wf:
        frames = wf.readframes(wf.getnframes())
        sample_rate = wf.getframerate()
        nchannels = wf.getnchannels()
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    sd.play(audio, samplerate=sample_rate)
    sd.wait()


def speak_async(text: str) -> None:
    threading.Thread(target=speak, args=(text,), daemon=True).start()
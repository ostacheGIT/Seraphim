"""
Synthèse vocale JARVIS via Coqui XTTS v2 — clonage vocal local.
"""

import io
import wave
import threading
import torch
import numpy as np
import sounddevice as sd
from pathlib import Path
from TTS.tts.configs.xtts_config import XttsConfig
from TTS.tts.models.xtts import Xtts

# ─── Chemin vers le sample vocal JARVIS ──────────────────────────────────────
VOICE_DIR     = Path(__file__).parent
JARVIS_SAMPLE = VOICE_DIR / "jarvis_EN-voice.mp3"
SAMPLE_RATE   = 24000

# ─── Singleton ────────────────────────────────────────────────────────────────
_model: Xtts | None = None
_gpt_cond_latent    = None
_speaker_embedding  = None
_lock = threading.Lock()


def _get_model() -> tuple[Xtts, object, object]:
    global _model, _gpt_cond_latent, _speaker_embedding
    if _model is None:
        with _lock:
            if _model is None:
                print("[Seraphim TTS] Chargement Coqui XTTS v2…")

                from TTS.utils.manage import ModelManager
                manager = ModelManager()
                model_path, config_path, _ = manager.download_model(
                    "tts_models/multilingual/multi-dataset/xtts_v2"
                )

                config = XttsConfig()
                config.load_json(config_path)

                _model = Xtts.init_from_config(config)
                _model.load_checkpoint(config, checkpoint_dir=model_path, eval=True)

                if torch.cuda.is_available():
                    _model.cuda()
                    print("[Seraphim TTS] GPU activé ✓")

                # ✅ Pré-calcul UNE SEULE FOIS des embeddings
                _gpt_cond_latent, _speaker_embedding = _model.get_conditioning_latents(
                    audio_path=[str(JARVIS_SAMPLE)]
                )
                print("[Seraphim TTS] Coqui XTTS v2 chargé ✓")

    return _model, _gpt_cond_latent, _speaker_embedding


# ─── Synthèse complète (WAV) ──────────────────────────────────────────────────

def synthesize_to_bytes(text: str) -> bytes:
    """Retourne les bytes WAV complets."""
    model, gpt_cond_latent, speaker_embedding = _get_model()

    outputs = model.inference(
        text=text,
        language="en",
        gpt_cond_latent=gpt_cond_latent,
        speaker_embedding=speaker_embedding,
        temperature=0.7,
    )

    wav = np.array(outputs["wav"], dtype=np.float32)
    pcm = (wav * 32767).astype(np.int16).tobytes()

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    return buf.getvalue()


# ─── Streaming chunk par chunk (PCM brut) ────────────────────────────────────

def synthesize_stream(text: str):
    """Générateur de chunks PCM int16 — pour StreamingResponse FastAPI."""
    model, gpt_cond_latent, speaker_embedding = _get_model()

    chunks = model.inference_stream(
        text=text,
        language="en",
        gpt_cond_latent=gpt_cond_latent,
        speaker_embedding=speaker_embedding,
        stream_chunk_size=20,
        temperature=0.7,
    )
    for chunk in chunks:
        yield (np.array(chunk) * 32767).astype(np.int16).tobytes()


# ─── Lecture locale ───────────────────────────────────────────────────────────

def speak(text: str) -> None:
    """Joue directement sur les haut-parleurs locaux (bloquant)."""
    wav_bytes = synthesize_to_bytes(text)
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, "rb") as wf:
        frames      = wf.readframes(wf.getnframes())
        sample_rate = wf.getframerate()
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    sd.play(audio, samplerate=sample_rate)
    sd.wait()


def speak_async(text: str) -> None:
    """Joue en arrière-plan (non bloquant)."""
    threading.Thread(target=speak, args=(text,), daemon=True).start()
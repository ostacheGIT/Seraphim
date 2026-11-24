import { useState, useRef, useCallback, useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";

// ─── Web Speech API types ─────────────────────────────────────────────────────

interface ISpeechRecognition extends EventTarget {
    lang: string;
    interimResults: boolean;
    maxAlternatives: number;
    continuous: boolean;
    start(): void;
    stop(): void;
    abort(): void;
    onstart: ((ev: Event) => void) | null;
    onend: ((ev: Event) => void) | null;
    onresult: ((ev: ISpeechRecognitionEvent) => void) | null;
    onerror: ((ev: ISpeechRecognitionErrorEvent) => void) | null;
}
interface ISpeechRecognitionEvent extends Event {
    results: { [i: number]: { [i: number]: { transcript: string; confidence: number }; length: number }; length: number };
    resultIndex: number;
}
interface ISpeechRecognitionErrorEvent extends Event { error: string; }
interface ISpeechRecognitionConstructor { new(): ISpeechRecognition; }

function getSR(): ISpeechRecognitionConstructor | null {
    if (typeof window === "undefined") return null;
    return (window as unknown as Record<string, ISpeechRecognitionConstructor>).SpeechRecognition
        || (window as unknown as Record<string, ISpeechRecognitionConstructor>).webkitSpeechRecognition
        || null;
}

// ─── Config ───────────────────────────────────────────────────────────────────

const SERAPHIM_API = "http://localhost:7272";
const WAKE_WORDS   = [
    "seraphim", "séraphim", "séraphin", "séraphine",
    "serafim",  "serafin",  "seraphin", "sérafin",
    "seraph",   "sera fim", "sara",
];

// ─── Wake word fuzzy matching ─────────────────────────────────────────────────
// French STT often splits "séraphim" phonetically into "séra film" (ph → f).
// We normalize both transcript and wake words before comparing.

function normalizeWW(s: string): string {
    // NFD splits accented letters into base + combining mark; the mark range is U+0300–U+036F
    const noDiacritics = s.normalize("NFD").split("").filter(c => {
        const cp = c.codePointAt(0) ?? 0;
        return cp < 0x0300 || cp > 0x036F;
    }).join("");
    return noDiacritics
        .toLowerCase()
        .replace(/ph/g, "f")    // ph → f (séraphim → serafim in French phonetics)
        .replace(/[^a-z]/g, ""); // letters only — strips spaces and punctuation
}

function editDistance(a: string, b: string): number {
    const dp: number[] = Array.from({ length: b.length + 1 }, (_, i) => i);
    for (let i = 1; i <= a.length; i++) {
        let prev = dp[0]++;
        for (let j = 1; j <= b.length; j++) {
            const next = a[i - 1] === b[j - 1] ? prev : 1 + Math.min(dp[j], dp[j - 1], prev);
            prev = dp[j];
            dp[j] = next;
        }
    }
    return dp[b.length];
}

// Sliding-window approximate match: is `word` contained in `text` within `d` edits?
function containsApprox(text: string, word: string, d: number): boolean {
    if (text.includes(word)) return true;
    const wl = word.length;
    for (let s = 0; s <= text.length - wl + d; s++) {
        for (let l = Math.max(1, wl - d); l <= wl + d && s + l <= text.length; l++) {
            if (editDistance(text.slice(s, s + l), word) <= d) return true;
        }
    }
    return false;
}

const _WAKE_NORMS = WAKE_WORDS.map(normalizeWW);

function hasWakeWord(rawText: string): boolean {
    const norm = normalizeWW(rawText);
    // Allow 1 edit for words > 5 chars (handles "serafilm" ↔ "serafim"), exact for short ones
    return _WAKE_NORMS.some((w) => containsApprox(norm, w, w.length > 5 ? 1 : 0));
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

interface UseSpeechOptions {
    lang?: string;
    onTranscript: (text: string) => void;
    onError?: (msg: string) => void;
}

export type SpeechState = "idle" | "listening" | "speaking" | "error";

export function useSpeech({ lang = "fr-FR", onTranscript, onError }: UseSpeechOptions) {
    const [state,              setState]              = useState<SpeechState>("idle");
    const [voiceError,         setVoiceError]         = useState<string | null>(null);
    const [isWakeWordActive,   setIsWakeWordActive]   = useState(false);
    const [wakeWordLastHeard,  setWakeWordLastHeard]  = useState<string>("");

    const onTranscriptRef = useRef(onTranscript);
    const onErrorRef      = useRef(onError);
    useEffect(() => { onTranscriptRef.current = onTranscript; }, [onTranscript]);
    useEffect(() => { onErrorRef.current      = onError;      }, [onError]);

    // TTS refs
    const audioCtxRef  = useRef<AudioContext | null>(null);
    const sourceRef    = useRef<AudioBufferSourceNode | null>(null);
    const abortTTSRef  = useRef<AbortController | null>(null);
    const queueRef     = useRef<string[]>([]);
    const isPlayingRef = useRef(false);

    // STT refs
    const recorderRef       = useRef<MediaRecorder | null>(null);
    const wakeWordRecogRef  = useRef<ISpeechRecognition | null>(null);
    const wakeWordActiveRef = useRef(false);

    // Stable ref to break circular dep: wakeWordLoop needs startListening, startListening needs wakeWordLoop
    const startListeningRef  = useRef<() => void>(() => {});
    const wakeWordLoopRef    = useRef<() => void>(() => {});

    // ── TTS ───────────────────────────────────────────────────────────────────

    const getAudioCtx = useCallback(async (): Promise<AudioContext> => {
        if (!audioCtxRef.current || audioCtxRef.current.state === "closed") {
            audioCtxRef.current = new AudioContext();
        }
        if (audioCtxRef.current.state === "suspended") await audioCtxRef.current.resume();
        return audioCtxRef.current;
    }, []);

    const stopCurrentAudio = useCallback(() => {
        abortTTSRef.current?.abort();
        abortTTSRef.current = null;
        try { sourceRef.current?.stop(); } catch (_) {}
        sourceRef.current = null;
    }, []);

    const playNext = useCallback(async () => {
        if (isPlayingRef.current || queueRef.current.length === 0) return;
        isPlayingRef.current = true;
        setState("speaking");
        const sentence = queueRef.current.shift()!;
        const abort    = new AbortController();
        abortTTSRef.current = abort;
        try {
            const res = await fetch(`${SERAPHIM_API}/tts/audio`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text: sentence }), signal: abort.signal,
            });
            if (!res.ok) throw new Error(`TTS ${res.status}`);
            const ab = await res.arrayBuffer();
            if (abort.signal.aborted) return;
            const ctx  = await getAudioCtx();
            const buf  = await ctx.decodeAudioData(ab);
            const src  = ctx.createBufferSource();
            sourceRef.current = src;
            src.buffer = buf; src.connect(ctx.destination);
            src.onended = () => {
                sourceRef.current = null; isPlayingRef.current = false; abortTTSRef.current = null;
                if (queueRef.current.length > 0) void playNext(); else setState("idle");
            };
            src.start(0);
        } catch (err) {
            if ((err as Error).name === "AbortError") return;
            sourceRef.current = null; isPlayingRef.current = false; abortTTSRef.current = null;
            if (queueRef.current.length > 0) void playNext(); else setState("idle");
            onError?.(`TTS indisponible : ${(err as Error).message}`);
        }
    }, [onError, getAudioCtx]);

    const speak = useCallback((text: string): Promise<void> => new Promise((resolve) => {
        const clean = text.replace(/\*\*(.+?)\*\*/g, "$1").replace(/\*(.+?)\*/g, "$1")
            .replace(/`(.+?)`/g, "$1").replace(/#{1,6}\s/g, "").trim();
        if (!clean) { resolve(); return; }
        queueRef.current.push(clean);
        void playNext();
        resolve();
    }), [playNext]);

    const stopSpeaking = useCallback(() => {
        queueRef.current = []; isPlayingRef.current = false;
        stopCurrentAudio(); setState("idle");
    }, [stopCurrentAudio]);

    // ── Wake word — short looping sessions (non-continuous to avoid conflicts) ─

    // Each session listens for a single utterance.
    // If it contains a wake word → trigger main STT.
    // Otherwise → immediately restart another session.
    // This way there is NEVER an abort() call; sessions end naturally → no conflict.
    const wakeWordLoop = useCallback(() => {
        if (!wakeWordActiveRef.current) return;
        const SR = getSR();
        if (!SR) return;

        const recog = new SR();
        recog.lang            = lang;
        recog.continuous      = false;
        recog.interimResults  = true;
        recog.maxAlternatives = 3; // check multiple hypotheses — STT may mis-transcribe "séraphim"

        let detected = false;

        recog.onresult = (e: ISpeechRecognitionEvent) => {
            if (detected) return;
            for (let ri = e.resultIndex; ri < e.results.length; ri++) {
                const result = e.results[ri];
                // Log all alternatives so you can open DevTools and see the actual transcripts
                const alts: string[] = [];
                for (let alt = 0; alt < result.length; alt++) {
                    alts.push(result[alt].transcript);
                }
                const top = alts[0] ?? "";
                console.log("[WakeWord]", alts.join(" | "), "→ norm:", normalizeWW(top));
                setWakeWordLastHeard(top);

                if (alts.some(t => hasWakeWord(t))) {
                    detected = true;
                    invoke("show_main_window").catch(() => {});
                    recog.stop();
                    break;
                }
            }
        };

        // onend always fires (after result, error, or timeout)
        recog.onend = () => {
            wakeWordRecogRef.current = null;
            if (detected) {
                // Let the browser release the mic, then start main STT
                setTimeout(() => startListeningRef.current(), 200);
            } else if (wakeWordActiveRef.current) {
                // No wake word heard, loop immediately
                wakeWordLoopRef.current();
            }
        };

        // onerror still fires before onend; just let onend handle the restart
        recog.onerror = () => { /* onend will fire next */ };

        try {
            recog.start();
            wakeWordRecogRef.current = recog;
        } catch (_) {
            // start() failed (e.g. mic in use), retry after a second
            if (wakeWordActiveRef.current) setTimeout(() => wakeWordLoopRef.current(), 1000);
        }
    }, [lang]);

    // Keep refs current
    useEffect(() => { wakeWordLoopRef.current = wakeWordLoop; }, [wakeWordLoop]);

    // ── STT : micro → Whisper local (backend /stt/transcribe) ─────────────────
    // La reconnaissance vocale du navigateur (Web Speech API) ne marche pas dans
    // la fenêtre Tauri (WebView2) : elle dépend d'un service cloud → erreur
    // « network ». On enregistre donc le micro ici et on transcrit en local.
    // Fin de l'enregistrement : 1,2 s de silence après la parole, 2e clic, ou 20 s.

    const startListening = useCallback(async () => {
        // Second clic pendant l'écoute : on arrête et on envoie ce qui a été dit
        if (recorderRef.current) {
            if (recorderRef.current.state === "recording") recorderRef.current.stop();
            return;
        }

        // Stop any in-progress wake word session without abort() —
        // null out onend so it won't restart itself after we kill it.
        if (wakeWordRecogRef.current) {
            const wr = wakeWordRecogRef.current;
            wakeWordRecogRef.current = null;
            wr.onend = null; wr.onerror = null; wr.onresult = null;
            try { wr.abort(); } catch (_) {}
        }

        queueRef.current = []; isPlayingRef.current = false;
        stopCurrentAudio(); setVoiceError(null);

        const fail = (msg: string) => {
            setVoiceError(msg); onErrorRef.current?.(msg); setState("idle");
        };

        let stream: MediaStream;
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: true, noiseSuppression: true },
            });
        } catch (err) {
            fail(`Accès micro impossible — autorise le micro dans les paramètres Windows (${(err as Error).name}).`);
            wakeWordLoopRef.current();
            return;
        }

        // Pendant que l'utilisateur parle : le backend relève les noms visibles à
        // l'écran (pour bien transcrire « README.md »…) et charge Whisper.
        void fetch(`${SERAPHIM_API}/stt/prime`, { method: "POST" }).catch(() => {});

        const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
        const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
        const chunks: Blob[] = [];
        rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };

        // Détection de silence (niveau RMS du micro)
        const meterCtx = new AudioContext();
        const analyser = meterCtx.createAnalyser();
        analyser.fftSize = 2048;
        meterCtx.createMediaStreamSource(stream).connect(analyser);
        const samples = new Float32Array(analyser.fftSize);
        const started = performance.now();
        let heard = false;
        let lastLoud = started;
        const timer = window.setInterval(() => {
            analyser.getFloatTimeDomainData(samples);
            let sum = 0;
            for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
            const rms = Math.sqrt(sum / samples.length);
            const now = performance.now();
            if (rms > 0.02) { heard = true; lastLoud = now; }
            const done = (heard && now - lastLoud > 1200) || (!heard && now - started > 7000) || now - started > 20000;
            if (done && rec.state === "recording") rec.stop();
        }, 100);

        rec.onstop = async () => {
            window.clearInterval(timer);
            stream.getTracks().forEach((t) => t.stop());
            void meterCtx.close();
            recorderRef.current = null;
            setState("idle");
            if (heard && chunks.length > 0) {
                try {
                    const form = new FormData();
                    form.append("audio", new Blob(chunks, { type: rec.mimeType || "audio/webm" }), "voice.webm");
                    const res = await fetch(`${SERAPHIM_API}/stt/transcribe`, { method: "POST", body: form });
                    if (!res.ok) throw new Error(`STT ${res.status}`);
                    const { text } = (await res.json()) as { text?: string };
                    if (text?.trim()) onTranscriptRef.current(text.trim());
                } catch (err) {
                    fail(`Transcription impossible : ${(err as Error).message}`);
                }
            }
            wakeWordLoopRef.current(); // resume wake word loop after STT ends
        };

        recorderRef.current = rec;
        rec.start(250);
        setState("listening");
    }, [stopCurrentAudio]);

    useEffect(() => { startListeningRef.current = () => { void startListening(); }; }, [startListening]);

    const stopListening = useCallback(() => {
        if (recorderRef.current?.state === "recording") recorderRef.current.stop();
        else setState("idle");
    }, []);

    const toggleListening = useCallback(() => {
        if (state === "listening") stopListening();
        else void startListening();
    }, [state, startListening, stopListening]);

    // ── Wake word toggle ──────────────────────────────────────────────────────

    const toggleWakeWord = useCallback(() => {
        if (wakeWordActiveRef.current) {
            wakeWordActiveRef.current = false;
            if (wakeWordRecogRef.current) {
                const wr = wakeWordRecogRef.current;
                wakeWordRecogRef.current = null;
                wr.onend = null; wr.onerror = null; wr.onresult = null;
                try { wr.abort(); } catch (_) {}
            }
            setIsWakeWordActive(false);
        } else {
            wakeWordActiveRef.current = true;
            setIsWakeWordActive(true);
            wakeWordLoopRef.current();
        }
    }, []);

    // ── Cleanup ───────────────────────────────────────────────────────────────

    useEffect(() => () => {
        wakeWordActiveRef.current = false;
        if (wakeWordRecogRef.current) {
            wakeWordRecogRef.current.onend = null;
            try { wakeWordRecogRef.current.abort(); } catch (_) {}
        }
        try { recorderRef.current?.stop(); } catch (_) {}
        queueRef.current = []; isPlayingRef.current = false;
        stopCurrentAudio(); audioCtxRef.current?.close();
    }, [stopCurrentAudio]);

    return {
        state,
        isListening:      state === "listening",
        isSpeaking:       state === "speaking",
        voiceError,
        isWakeWordActive,
        wakeWordLastHeard,
        whisperAvailable: null as null,
        toggleListening,
        startListening,
        stopListening,
        toggleWakeWord,
        speak,
        stopSpeaking,
    };
}

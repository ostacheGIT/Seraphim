import { useState, useRef, useCallback, useEffect } from "react";

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
    results: { [i: number]: { [i: number]: { transcript: string; confidence: number } }; length: number };
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

// ─── Hook ─────────────────────────────────────────────────────────────────────

interface UseSpeechOptions {
    lang?: string;
    onTranscript: (text: string) => void;
    onError?: (msg: string) => void;
}

export type SpeechState = "idle" | "listening" | "speaking" | "error";

export function useSpeech({ lang = "fr-FR", onTranscript, onError }: UseSpeechOptions) {
    const [state,            setState]            = useState<SpeechState>("idle");
    const [voiceError,       setVoiceError]       = useState<string | null>(null);
    const [isWakeWordActive, setIsWakeWordActive] = useState(false);

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
    const recogRef          = useRef<ISpeechRecognition | null>(null);
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
        recog.lang            = lang; // same language as main STT
        recog.continuous      = false;
        recog.interimResults  = true;  // fire on partial results — catch word mid-utterance
        recog.maxAlternatives = 1;

        let detected = false;

        recog.onresult = (e: ISpeechRecognitionEvent) => {
            if (detected) return;
            for (let ri = e.resultIndex; ri < e.results.length; ri++) {
                const text = e.results[ri][0].transcript.toLowerCase();
                if (WAKE_WORDS.some(w => text.includes(w))) {
                    detected = true;
                    recog.stop(); // stop cleanly so onend fires normally
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

    // ── STT (Web Speech API) ──────────────────────────────────────────────────

    const startListening = useCallback(() => {
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

        if (recogRef.current) {
            try { recogRef.current.abort(); } catch (_) {}
            recogRef.current = null;
        }

        const SR = getSR();
        if (!SR) {
            const msg = "SpeechRecognition non supporté. Utilise Chrome ou Edge.";
            setVoiceError(msg); onError?.(msg); return;
        }

        const recog = new SR();
        recog.lang = lang; recog.interimResults = false; recog.maxAlternatives = 1; recog.continuous = false;

        recog.onstart  = () => { setVoiceError(null); setState("listening"); };
        recog.onresult = (e: ISpeechRecognitionEvent) => {
            const t = e.results[0][0].transcript.trim();
            if (t) onTranscriptRef.current(t);
        };
        recog.onerror = (e: ISpeechRecognitionErrorEvent) => {
            const msg = e.error === "not-allowed"
                ? "Accès micro refusé — autorise le micro dans les paramètres Windows."
                : e.error === "no-speech" ? null : e.error;
            if (msg) { setVoiceError(msg); onErrorRef.current?.(msg); }
            setState("idle");
        };
        recog.onend = () => {
            setState((s) => (s === "listening" ? "idle" : s));
            wakeWordLoopRef.current(); // resume wake word loop after STT ends
        };

        recogRef.current = recog;
        try { recog.start(); } catch (err) {
            const msg = `Impossible de démarrer : ${(err as Error).message}`;
            setVoiceError(msg); onError?.(msg); setState("idle");
        }
    }, [lang, stopCurrentAudio, onError]);

    useEffect(() => { startListeningRef.current = startListening; }, [startListening]);

    const stopListening = useCallback(() => {
        recogRef.current?.stop(); setState("idle");
    }, []);

    const toggleListening = useCallback(() => {
        if (state === "listening") stopListening();
        else startListening();
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
        try { recogRef.current?.abort(); } catch (_) {}
        queueRef.current = []; isPlayingRef.current = false;
        stopCurrentAudio(); audioCtxRef.current?.close();
    }, [stopCurrentAudio]);

    return {
        state,
        isListening:      state === "listening",
        isSpeaking:       state === "speaking",
        voiceError,
        isWakeWordActive,
        whisperAvailable: null as null,
        toggleListening,
        startListening,
        stopListening,
        toggleWakeWord,
        speak,
        stopSpeaking,
    };
}

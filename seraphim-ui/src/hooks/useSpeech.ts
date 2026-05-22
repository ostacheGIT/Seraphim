import { useState, useRef, useCallback, useEffect } from "react";

// ─── Types Web Speech API ─────────────────────────────────────────────────────

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
    results: { [i: number]: { [i: number]: { transcript: string; confidence: number } } };
}
interface ISpeechRecognitionErrorEvent extends Event {
    error: string;
}
interface ISpeechRecognitionConstructor { new(): ISpeechRecognition; }

function getSR(): ISpeechRecognitionConstructor | null {
    if (typeof window === "undefined") return null;
    return (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition || null;
}

// ─── Config backend ───────────────────────────────────────────────────────────

const SERAPHIM_API = "http://localhost:7272";

// ─── Hook principal ───────────────────────────────────────────────────────────

interface UseSpeechOptions {
    lang?: string;
    onTranscript: (text: string) => void;
    onError?: (msg: string) => void;
}

export type SpeechState = "idle" | "listening" | "speaking" | "error";

export function useSpeech({
                              lang = "fr-FR",
                              onTranscript,
                              onError,
                          }: UseSpeechOptions) {
    const [state, setState]   = useState<SpeechState>("idle");
    const [voiceError, setVoiceError] = useState<string | null>(null);
    const recogRef            = useRef<ISpeechRecognition | null>(null);
    const audioCtxRef         = useRef<AudioContext | null>(null);
    const sourceRef           = useRef<AudioBufferSourceNode | null>(null);
    const queueRef            = useRef<string[]>([]);
    const isPlayingRef        = useRef<boolean>(false);
    const abortRef            = useRef<AbortController | null>(null);

    // Always-fresh refs — avoids stale-closure bugs when activeId changes right
    // before startListening() is called (e.g. orb click with no open conversation).
    const onTranscriptRef = useRef(onTranscript);
    const onErrorRef      = useRef(onError);
    useEffect(() => { onTranscriptRef.current = onTranscript; }, [onTranscript]);
    useEffect(() => { onErrorRef.current      = onError;      }, [onError]);

    // ── Helpers ───────────────────────────────────────────────────────────────

    const getAudioCtx = useCallback(async (): Promise<AudioContext> => {
        if (!audioCtxRef.current || audioCtxRef.current.state === "closed") {
            audioCtxRef.current = new AudioContext();
        }
        if (audioCtxRef.current.state === "suspended") {
            await audioCtxRef.current.resume();
        }
        return audioCtxRef.current;
    }, []);

    const stopCurrentAudio = useCallback(() => {
        abortRef.current?.abort();
        abortRef.current = null;
        try { sourceRef.current?.stop(); } catch (_) {}
        sourceRef.current = null;
    }, []);

    // ── STT ───────────────────────────────────────────────────────────────────

    const startListening = useCallback(() => {
        queueRef.current     = [];
        isPlayingRef.current = false;
        stopCurrentAudio();
        setVoiceError(null);

        // Stop any previous recognition session before starting a new one
        if (recogRef.current) {
            try { recogRef.current.abort(); } catch (_) {}
            recogRef.current = null;
        }

        const SR = getSR();
        if (!SR) {
            const msg = "SpeechRecognition non supporté. Utilise Chrome ou Edge.";
            setVoiceError(msg);
            onError?.(msg);
            return;
        }

        const recog = new SR();
        recog.lang            = lang;
        recog.interimResults  = false;
        recog.maxAlternatives = 1;
        recog.continuous      = false;

        recog.onstart  = () => { setVoiceError(null); setState("listening"); };
        recog.onresult = (e: ISpeechRecognitionEvent) => {
            const transcript = e.results[0][0].transcript.trim();
            if (transcript) onTranscriptRef.current(transcript);
        };
        recog.onerror  = (e: ISpeechRecognitionErrorEvent) => {
            const msg = e.error === "not-allowed"
                ? "Accès micro refusé — autorise le micro dans les paramètres Windows."
                : e.error === "no-speech"
                ? null
                : e.error;
            if (msg) { setVoiceError(msg); onErrorRef.current?.(msg); }
            setState("idle");
        };
        recog.onend    = () => {
            setState((s) => (s === "listening" ? "idle" : s));
        };

        recogRef.current = recog;
        try {
            recog.start();
        } catch (err) {
            const msg = `Impossible de démarrer la reconnaissance : ${(err as Error).message}`;
            setVoiceError(msg);
            onError?.(msg);
            setState("idle");
        }
    }, [lang, stopCurrentAudio]);

    const stopListening   = useCallback(() => { recogRef.current?.stop(); setState("idle"); }, []);
    const toggleListening = useCallback(() => {
        if (state === "listening") stopListening();
        else startListening();
    }, [state, startListening, stopListening]);

    // ── Queue audio ───────────────────────────────────────────────────────────

    const playNext = useCallback(async () => {
        if (isPlayingRef.current || queueRef.current.length === 0) return;
        isPlayingRef.current = true;
        setState("speaking");

        const sentence = queueRef.current.shift()!;
        const abort    = new AbortController();
        abortRef.current = abort;

        try {
            const response = await fetch(`${SERAPHIM_API}/tts/audio`, {
                method:  "POST",
                headers: { "Content-Type": "application/json" },
                body:    JSON.stringify({ text: sentence }),
                signal:  abort.signal,
            });

            if (!response.ok) throw new Error(`TTS error: ${response.status}`);

            const arrayBuffer = await response.arrayBuffer();
            if (abort.signal.aborted) return;

            const audioCtx    = await getAudioCtx();
            const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
            const source      = audioCtx.createBufferSource();
            sourceRef.current = source;
            source.buffer     = audioBuffer;
            source.connect(audioCtx.destination);

            source.onended = () => {
                sourceRef.current    = null;
                isPlayingRef.current = false;
                abortRef.current     = null;
                if (queueRef.current.length > 0) playNext();
                else setState("idle");
            };

            source.start(0);

        } catch (err) {
            if ((err as Error).name === "AbortError") return;
            sourceRef.current    = null;
            isPlayingRef.current = false;
            abortRef.current     = null;
            if (queueRef.current.length > 0) playNext();
            else setState("idle");
            onError?.(`TTS indisponible : ${(err as Error).message}`);
        }
    }, [onError, getAudioCtx]);

    // ── TTS — speak() ajoute à la queue ──────────────────────────────────────

    const speak = useCallback(
        (text: string): Promise<void> => {
            return new Promise((resolve) => {
                const clean = text
                    .replace(/\*\*(.+?)\*\*/g, "$1")
                    .replace(/\*(.+?)\*/g,     "$1")
                    .replace(/`(.+?)`/g,        "$1")
                    .replace(/#{1,6}\s/g,       "")
                    .trim();

                if (!clean) { resolve(); return; }

                queueRef.current.push(clean);
                playNext();
                resolve();
            });
        },
        [playNext]
    );

    // ── Stop speaking ─────────────────────────────────────────────────────────

    const stopSpeaking = useCallback(() => {
        queueRef.current     = [];
        isPlayingRef.current = false;
        stopCurrentAudio();
        setState("idle");
    }, [stopCurrentAudio]);

    // ── Nettoyage à l'unmount ─────────────────────────────────────────────────

    useEffect(() => {
        return () => {
            recogRef.current?.abort();
            queueRef.current     = [];
            isPlayingRef.current = false;
            stopCurrentAudio();
            audioCtxRef.current?.close();
        };
    }, [stopCurrentAudio]);

    return {
        state,
        isListening:     state === "listening",
        isSpeaking:      state === "speaking",
        voiceError,
        toggleListening,
        startListening,
        stopListening,
        speak,
        stopSpeaking,
    };
}
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

export type SpeechState = "idle" | "listening" | "speaking";

export function useSpeech({
                              lang = "fr-FR",
                              onTranscript,
                              onError,
                          }: UseSpeechOptions) {
    const [state, setState] = useState<SpeechState>("idle");
    const recogRef      = useRef<ISpeechRecognition | null>(null);
    const audioCtxRef   = useRef<AudioContext | null>(null);
    const sourceRef     = useRef<AudioBufferSourceNode | null>(null);
    const queueRef      = useRef<string[]>([]);
    const isPlayingRef  = useRef<boolean>(false);

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
        try { sourceRef.current?.stop(); } catch (_) {}
        sourceRef.current = null;
    }, []);

    // ── STT ───────────────────────────────────────────────────────────────────

    const startListening = useCallback(() => {
        // Vider la queue et stopper l'audio si Seraphim parle encore
        queueRef.current = [];
        isPlayingRef.current = false;
        stopCurrentAudio();
        setState("idle");

        const SR = getSR();
        if (!SR) {
            onError?.("SpeechRecognition non supporté. Utilise Chrome ou Edge.");
            return;
        }

        const recog = new SR();
        recog.lang            = lang;
        recog.interimResults  = false;
        recog.maxAlternatives = 1;
        recog.continuous      = false;

        recog.onstart = () => setState("listening");

        recog.onresult = (e: ISpeechRecognitionEvent) => {
            const transcript = e.results[0][0].transcript.trim();
            if (transcript) onTranscript(transcript);
        };

        recog.onerror = (e: ISpeechRecognitionErrorEvent) => {
            onError?.(e.error);
            setState("idle");
        };

        recog.onend = () => {
            setState((s) => (s === "listening" ? "idle" : s));
        };

        recogRef.current = recog;
        recog.start();
    }, [lang, onTranscript, onError, stopCurrentAudio]);

    const stopListening = useCallback(() => {
        recogRef.current?.stop();
        setState("idle");
    }, []);

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

        try {
            const response = await fetch(`${SERAPHIM_API}/tts/audio`, {
                method:  "POST",
                headers: { "Content-Type": "application/json" },
                body:    JSON.stringify({ text: sentence }),
            });

            if (!response.ok) throw new Error(`TTS error: ${response.status}`);

            const arrayBuffer = await response.arrayBuffer();
            const audioCtx    = await getAudioCtx();
            const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
            const source      = audioCtx.createBufferSource();
            sourceRef.current = source;
            source.buffer     = audioBuffer;
            source.connect(audioCtx.destination);

            source.onended = () => {
                sourceRef.current  = null;
                isPlayingRef.current = false;
                if (queueRef.current.length > 0) {
                    playNext();
                } else {
                    setState("idle");
                }
            };

            source.start(0);
        } catch (err) {
            sourceRef.current    = null;
            isPlayingRef.current = false;
            if (queueRef.current.length > 0) {
                playNext();
            } else {
                setState("idle");
            }
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
                resolve(); // non-bloquant — la queue gère l'ordre
            });
        },
        [playNext]
    );

    // ── Stop speaking — vide la queue et arrête tout ──────────────────────────

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
        toggleListening,
        startListening,
        stopListening,
        speak,
        stopSpeaking,
    };
}
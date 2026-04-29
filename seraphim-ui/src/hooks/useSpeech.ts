import { useState, useRef, useCallback, useEffect } from "react";

// ─── Types Web Speech API (non inclus dans lib.dom par défaut) ───────────────

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

// ─── Config backend ──────────────────────────────────────────────────────────

const SERAPHIM_API = "http://localhost:7272";

// ─── Hook principal ──────────────────────────────────────────────────────────

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
    const recogRef = useRef<ISpeechRecognition | null>(null);
    const audioCtxRef = useRef<AudioContext | null>(null);
    const sourceRef = useRef<AudioBufferSourceNode | null>(null);

    // ── Écoute (STT) ─────────────────────────────────────────────────────────

    const startListening = useCallback(() => {
        // Interrompre Piper si Seraphim parle encore
        if (sourceRef.current) {
            sourceRef.current.stop();
            sourceRef.current = null;
        }
        audioCtxRef.current?.close();
        audioCtxRef.current = null;
        setState("idle");

        const SR = getSR();
        if (!SR) {
            onError?.("SpeechRecognition non supporté. Utilise Chrome ou Edge.");
            return;
        }

        const recog = new SR();
        recog.lang = lang;
        recog.interimResults = false;
        recog.maxAlternatives = 1;
        recog.continuous = false;

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
    }, [lang, onTranscript, onError]);

    const stopListening = useCallback(() => {
        recogRef.current?.stop();
        setState("idle");
    }, []);

    const toggleListening = useCallback(() => {
        if (state === "listening") stopListening();
        else startListening();
    }, [state, startListening, stopListening]);

    // ── Synthèse vocale via Piper JARVIS (AudioContext) ───────────────────────

    const speak = useCallback(
        (text: string): Promise<void> => {
            return new Promise(async (resolve) => {
                // Annuler toute lecture en cours
                if (sourceRef.current) {
                    sourceRef.current.stop();
                    sourceRef.current = null;
                }
                audioCtxRef.current?.close();
                audioCtxRef.current = null;

                const clean = text
                    .replace(/\*\*(.+?)\*\*/g, "$1")
                    .replace(/\*(.+?)\*/g, "$1")
                    .replace(/`(.+?)`/g, "$1")
                    .replace(/#{1,6}\s/g, "")
                    .trim();

                if (!clean) { resolve(); return; }

                try {
                    setState("speaking");

                    const response = await fetch(`${SERAPHIM_API}/tts/audio`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ text: clean }),
                    });

                    if (!response.ok) throw new Error(`TTS error: ${response.status}`);

                    const arrayBuffer = await response.arrayBuffer();

                    // AudioContext contourne la politique autoplay de WebView2
                    const audioCtx = new AudioContext();
                    audioCtxRef.current = audioCtx;

                    const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
                    const source = audioCtx.createBufferSource();
                    sourceRef.current = source;
                    source.buffer = audioBuffer;
                    source.connect(audioCtx.destination);

                    source.onended = () => {
                        audioCtx.close();
                        audioCtxRef.current = null;
                        sourceRef.current = null;
                        setState("idle");
                        resolve();
                    };

                    source.start(0);
                } catch (err) {
                    audioCtxRef.current?.close();
                    audioCtxRef.current = null;
                    sourceRef.current = null;
                    setState("idle");
                    onError?.(`TTS indisponible : ${(err as Error).message}`);
                    resolve();
                }
            });
        },
        [onError]
    );

    // ── Stop speaking ─────────────────────────────────────────────────────────

    const stopSpeaking = useCallback(() => {
        if (sourceRef.current) {
            sourceRef.current.stop();
            sourceRef.current = null;
        }
        audioCtxRef.current?.close();
        audioCtxRef.current = null;
        setState("idle");
    }, []);

    // ── Nettoyage à l'unmount ─────────────────────────────────────────────────

    useEffect(() => {
        return () => {
            recogRef.current?.abort();
            if (sourceRef.current) {
                sourceRef.current.stop();
                sourceRef.current = null;
            }
            audioCtxRef.current?.close();
        };
    }, []);

    return {
        state,
        isListening: state === "listening",
        isSpeaking:  state === "speaking",
        toggleListening,
        startListening,
        stopListening,
        speak,
        stopSpeaking,
    };
}
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

// ─── Hook principal ──────────────────────────────────────────────────────────

interface UseSpeechOptions {
    lang?: string;          // langue (défaut : fr-FR)
    voiceName?: string;     // nom exact de la voix TTS souhaitée (optionnel)
    rate?: number;          // vitesse TTS 0.5 – 2  (défaut : 1)
    pitch?: number;         // tonalité TTS 0 – 2   (défaut : 1)
    onTranscript: (text: string) => void;   // appelé quand l'utilisateur a parlé
    onError?: (msg: string) => void;
}

export type SpeechState = "idle" | "listening" | "speaking";

export function useSpeech({
                              lang = "fr-FR",
                              voiceName,
                              rate = 1,
                              pitch = 1,
                              onTranscript,
                              onError,
                          }: UseSpeechOptions) {
    const [state, setState] = useState<SpeechState>("idle");
    const recogRef = useRef<ISpeechRecognition | null>(null);
    const synthRef = useRef<SpeechSynthesis | null>(null);
    const voiceRef = useRef<SpeechSynthesisVoice | null>(null);

    // Charger la voix préférée dès que la liste est disponible
    useEffect(() => {
        if (typeof window === "undefined" || !window.speechSynthesis) return;
        synthRef.current = window.speechSynthesis;

        const pickVoice = () => {
            const voices = synthRef.current!.getVoices();
            if (!voices.length) return;

            if (voiceName) {
                voiceRef.current = voices.find((v) => v.name === voiceName) ?? null;
            }
            // Fallback : première voix française disponible
            if (!voiceRef.current) {
                voiceRef.current =
                    voices.find((v) => v.lang.startsWith("fr") && v.localService) ??
                    voices.find((v) => v.lang.startsWith("fr")) ??
                    null;
            }
        };

        pickVoice();
        // Chrome charge les voix de façon asynchrone
        synthRef.current.onvoiceschanged = pickVoice;
    }, [voiceName]);

    // ── Écoute ──────────────────────────────────────────────────────────────────
    const startListening = useCallback(() => {
        // Interrompre la synthèse si Seraphim est encore en train de parler
        synthRef.current?.cancel();

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
            // On repasse idle ici ; speak() le fera passer sur "speaking" si besoin
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

    // ── Synthèse vocale ─────────────────────────────────────────────────────────
    const speak = useCallback(
        (text: string): Promise<void> => {
            return new Promise((resolve) => {
                const synth = synthRef.current;
                if (!synth) { resolve(); return; }

                // Annuler toute parole en cours
                synth.cancel();

                // Nettoyer le texte (retirer les caractères markdown basiques)
                const clean = text
                    .replace(/\*\*(.+?)\*\*/g, "$1")
                    .replace(/\*(.+?)\*/g, "$1")
                    .replace(/`(.+?)`/g, "$1")
                    .replace(/#{1,6}\s/g, "")
                    .trim();

                const utter = new SpeechSynthesisUtterance(clean);
                utter.lang = lang;
                utter.rate = rate;
                utter.pitch = pitch;
                if (voiceRef.current) utter.voice = voiceRef.current;

                utter.onstart = () => setState("speaking");
                utter.onend = () => { setState("idle"); resolve(); };
                utter.onerror = () => { setState("idle"); resolve(); };

                synth.speak(utter);
            });
        },
        [lang, rate, pitch]
    );

    // Arrêter la synthèse en cours
    const stopSpeaking = useCallback(() => {
        synthRef.current?.cancel();
        setState("idle");
    }, []);

    // Nettoyage à l'unmount
    useEffect(() => {
        return () => {
            recogRef.current?.abort();
            synthRef.current?.cancel();
        };
    }, []);

    return {
        state,         // "idle" | "listening" | "speaking"
        isListening: state === "listening",
        isSpeaking:  state === "speaking",
        toggleListening,
        startListening,
        stopListening,
        speak,
        stopSpeaking,
    };
}
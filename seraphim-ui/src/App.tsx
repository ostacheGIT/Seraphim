import { useState, useCallback, useRef, useEffect } from "react";
import { useConversation } from "./hooks/useConversation";
import { useSpeech } from "./hooks/useSpeech";
import { askSeraphim, warmupEngine, generateSessionTitle } from "./hooks/useSeraphimBackend";
import { useTheme } from "./hooks/useTheme";
import OrbScreen from "./components/OrbScreen";

export default function App() {
    const [isThinking, setIsThinking] = useState(false);
    const [agentId, setAgentId] = useState<string>("auto");
    const [pendingImage, setPendingImage] = useState<string | null>(null);
    const [pendingFile, setPendingFile] = useState<{ name: string; content: string } | null>(null);

    const { theme, toggleTheme } = useTheme();

    const {
        conversations,
        activeId,
        active,
        engineId,
        setEngineId,
        setActiveId,
        newConversation,
        deleteConversation,
        addMessage,
        updateMessage,
        replaceFromMessage,
        truncateMessages,
        updateConversationTitle,
    } = useConversation();

    const speakRef = useRef<((text: string) => Promise<void>) | null>(null);
    const abortRef = useRef<AbortController | null>(null);

    const {
        isListening,
        isSpeaking,
        voiceError,
        isWakeWordActive,
        wakeWordLastHeard,
        whisperAvailable,
        toggleListening,
        toggleWakeWord,
        speak,
        stopSpeaking,
    } = useSpeech({
        lang: navigator.language || "fr-FR",
        onTranscript: (transcript) => sendMessage(transcript),
        onError: (err) => console.error("Speech error:", err),
    });

    speakRef.current = speak;

    // Stable ref so the Tauri event listener never re-registers when state changes
    const toggleListeningRef = useRef(toggleListening);
    useEffect(() => { toggleListeningRef.current = toggleListening; }, [toggleListening]);

    // Global Ctrl+Space shortcut emitted by Tauri Rust side
    useEffect(() => {
        let unlisten: (() => void) | undefined;
        import("@tauri-apps/api/event")
            .then(({ listen }) => listen("toggle-listening", () => toggleListeningRef.current()))
            .then((fn) => { unlisten = fn; })
            .catch(() => { /* not running in Tauri — ignore */ });
        return () => { unlisten?.(); };
    }, []);

    // Request desktop notification permission once
    useEffect(() => {
        if ("Notification" in window && Notification.permission === "default") {
            Notification.requestPermission();
        }
    }, []);

    // Pre-load the model into Ollama memory whenever the engine changes
    useEffect(() => {
        warmupEngine(engineId);
    }, [engineId]);

    const sendMessage = useCallback(
        async (text: string) => {
            const trimmed = text.trim();
            if ((!trimmed && !pendingImage && !pendingFile) || isThinking) return;
            const imageSnapshot = pendingImage;
            const fileSnapshot = pendingFile;
            const sendStart = Date.now();
            setPendingImage(null);
            setPendingFile(null);

            const isFirstExchange = !active || active.messages.filter((m) => m.role === "user").length === 0;
            const currentSessionId = activeId;

            // Prepend file content to message if present
            const fullText = fileSnapshot
                ? `${trimmed || "Analyse ce fichier et résume son contenu."}\n\n[Fichier joint: ${fileSnapshot.name}]\n\n${fileSnapshot.content}`
                : trimmed;

            const imageDataUrl = imageSnapshot ? `data:image/png;base64,${imageSnapshot}` : undefined;
            addMessage(trimmed || (fileSnapshot ? `📎 ${fileSnapshot.name}` : "📎 Image"), "user", undefined, undefined, imageDataUrl);
            const abortCtrl = new AbortController();
            abortRef.current = abortCtrl;
            setIsThinking(true);
            let assistantMsgId: string | null = null;
            let accumulated = "";
            let rafHandle: number | null = null;

            const flushToState = () => {
                rafHandle = null;
                if (assistantMsgId === null) {
                    assistantMsgId = addMessage(accumulated, "assistant", "streaming");
                } else {
                    updateMessage(assistantMsgId, accumulated, "streaming");
                }
            };

            try {
                const { response, traceId } = await askSeraphim(
                    fullText || "Analyse cette image.",
                    activeId ?? undefined,
                    (token) => {
                        accumulated += token;
                        if (rafHandle === null) {
                            rafHandle = requestAnimationFrame(flushToState);
                        }
                    },
                    (sentence) => speakRef.current?.(sentence),
                    engineId,
                    agentId,
                    imageSnapshot ?? undefined,
                    undefined,
                    abortCtrl.signal,
                );
                if (rafHandle !== null) {
                    cancelAnimationFrame(rafHandle);
                    rafHandle = null;
                }
                if (response || assistantMsgId === null) {
                    if (assistantMsgId === null) {
                        if (response) addMessage(response, "assistant", "done", traceId ?? undefined);
                    } else {
                        updateMessage(assistantMsgId, response, "done", traceId ?? undefined);
                    }
                } else if (assistantMsgId !== null) {
                    updateMessage(assistantMsgId, accumulated, "done");
                }
                // Generate LLM title after first exchange — pass text directly so the backend
                // skips the DB lookup (faster), messages are already saved by this point.
                if (isFirstExchange && currentSessionId) {
                    generateSessionTitle(currentSessionId, trimmed ? trimmed : undefined).then((title) => {
                        if (title) updateConversationTitle(currentSessionId, title);
                    });
                }
            } catch {
                if (rafHandle !== null) cancelAnimationFrame(rafHandle);
                const errMsg = "Erreur : impossible de contacter le backend Seraphim.";
                if (assistantMsgId === null) {
                    addMessage(errMsg, "assistant", "error");
                } else {
                    updateMessage(assistantMsgId, errMsg, "error");
                }
                await speakRef.current?.(errMsg);
            } finally {
                abortRef.current = null;
                setIsThinking(false);
                const elapsed = Date.now() - sendStart;
                if (
                    elapsed > 3000 &&
                    !document.hasFocus() &&
                    "Notification" in window &&
                    Notification.permission === "granted"
                ) {
                    new Notification("Seraphim", {
                        body: "Votre réponse est prête.",
                        silent: true,
                    });
                }
            }
        },
        [isThinking, addMessage, updateMessage, activeId, active, engineId, agentId, pendingImage, pendingFile, updateConversationTitle],
    );

    const editMessage = useCallback(
        async (messageId: string, newContent: string) => {
            if (isThinking || !activeId || !active) return;
            const msgs = active.messages;
            const idx = msgs.findIndex((m) => m.id === messageId);
            if (idx === -1) return;

            const contextMessages = msgs
                .slice(0, idx)
                .map((m) => ({ role: m.role, content: m.content }));

            const dbKeepCount = msgs.slice(0, idx).filter((m) => m.role === "user").length * 2;

            replaceFromMessage(messageId, newContent);
            await truncateMessages(activeId, dbKeepCount);

            const abortCtrl = new AbortController();
            abortRef.current = abortCtrl;
            setIsThinking(true);
            let assistantMsgId: string | null = null;
            let accumulated = "";
            let rafHandle: number | null = null;

            const flushToState = () => {
                rafHandle = null;
                if (assistantMsgId === null) {
                    assistantMsgId = addMessage(accumulated, "assistant", "streaming");
                } else {
                    updateMessage(assistantMsgId, accumulated, "streaming");
                }
            };

            try {
                const { response, traceId } = await askSeraphim(
                    newContent,
                    activeId,
                    (token) => {
                        accumulated += token;
                        if (rafHandle === null) {
                            rafHandle = requestAnimationFrame(flushToState);
                        }
                    },
                    (sentence) => speakRef.current?.(sentence),
                    engineId,
                    agentId,
                    undefined,
                    contextMessages,
                    abortCtrl.signal,
                );
                if (rafHandle !== null) {
                    cancelAnimationFrame(rafHandle);
                    rafHandle = null;
                }
                if (response || assistantMsgId === null) {
                    if (assistantMsgId === null) {
                        if (response) addMessage(response, "assistant", "done", traceId ?? undefined);
                    } else {
                        updateMessage(assistantMsgId, response, "done", traceId ?? undefined);
                    }
                } else if (assistantMsgId !== null) {
                    updateMessage(assistantMsgId, accumulated, "done");
                }
            } catch {
                if (rafHandle !== null) cancelAnimationFrame(rafHandle);
                const errMsg = "Erreur : impossible de contacter le backend Seraphim.";
                if (assistantMsgId === null) {
                    addMessage(errMsg, "assistant", "error");
                } else {
                    updateMessage(assistantMsgId, errMsg, "error");
                }
            } finally {
                abortRef.current = null;
                setIsThinking(false);
            }
        },
        [isThinking, active, activeId, replaceFromMessage, truncateMessages, addMessage, updateMessage, engineId, agentId],
    );

    const stopGeneration = useCallback(() => {
        abortRef.current?.abort();
    }, []);

    return (
        <OrbScreen
            conversation={active}
            conversations={conversations}
            activeId={activeId}
            isListening={isListening}
            isThinking={isThinking}
            isSpeaking={isSpeaking}
            voiceError={voiceError}
            isWakeWordActive={isWakeWordActive}
            wakeWordLastHeard={wakeWordLastHeard}
            whisperAvailable={whisperAvailable}
            onSend={sendMessage}
            onVoiceToggle={toggleListening}
            onStopSpeaking={stopSpeaking}
            onWakeWordToggle={toggleWakeWord}
            onStop={stopGeneration}
            onSelectConversation={setActiveId}
            onNewConversation={newConversation}
            onDeleteConversation={deleteConversation}
            onEditMessage={editMessage}
            engineId={engineId}
            onEngineChange={setEngineId}
            agentId={agentId}
            onAgentChange={setAgentId}
            pendingImage={pendingImage}
            onImageChange={setPendingImage}
            pendingFile={pendingFile}
            onFileChange={setPendingFile}
            theme={theme}
            onThemeToggle={toggleTheme}
        />
    );
}

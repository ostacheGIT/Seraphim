import { useState, useCallback, useRef } from "react";
import { useConversation } from "./hooks/useConversation";
import { useSpeech } from "./hooks/useSpeech";
import { askSeraphim } from "./hooks/useSeraphimBackend";
import OrbScreen from "./components/OrbScreen";

export default function App() {
    const [input, setInput] = useState("");
    const [isThinking, setIsThinking] = useState(false);
    const [agentId, setAgentId] = useState<string>("auto");
    const [pendingImage, setPendingImage] = useState<string | null>(null);

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
    } = useConversation();

    const speakRef = useRef<((text: string) => Promise<void>) | null>(null);
    const abortRef = useRef<AbortController | null>(null);

    const {
        isListening,
        isSpeaking,
        toggleListening,
        speak,
        stopSpeaking,
    } = useSpeech({
        lang: "fr-FR",
        onTranscript: (transcript) => sendMessage(transcript),
        onError: (err) => console.error("Speech error:", err),
    });

    speakRef.current = speak;

    const sendMessage = useCallback(
        async (text: string) => {
            const trimmed = text.trim();
            if ((!trimmed && !pendingImage) || isThinking) return;
            const imageSnapshot = pendingImage;
            setInput("");
            setPendingImage(null);
            const imageDataUrl = imageSnapshot ? `data:image/png;base64,${imageSnapshot}` : undefined;
            addMessage(trimmed || "📎 Image", "user", undefined, undefined, imageDataUrl);
            const abortCtrl = new AbortController();
            abortRef.current = abortCtrl;
            setIsThinking(true);
            let assistantMsgId: string | null = null;
            let accumulated = "";
            try {
                const { response, traceId } = await askSeraphim(
                    trimmed || "Analyse cette image.",
                    activeId ?? undefined,
                    (token) => {
                        accumulated += token;
                        if (assistantMsgId === null) {
                            assistantMsgId = addMessage(accumulated, "assistant", "streaming");
                        } else {
                            updateMessage(assistantMsgId, accumulated, "streaming");
                        }
                    },
                    (sentence) => speakRef.current?.(sentence),
                    engineId,
                    agentId,
                    imageSnapshot ?? undefined,
                    undefined,
                    abortCtrl.signal,
                );
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
            }
        },
        [isThinking, addMessage, updateMessage, activeId, engineId, agentId, pendingImage],
    );

    const editMessage = useCallback(
        async (messageId: string, newContent: string) => {
            if (isThinking || !activeId || !active) return;
            const msgs = active.messages;
            const idx = msgs.findIndex((m) => m.id === messageId);
            if (idx === -1) return;

            // Context = messages before the edited one (for backend override)
            const contextMessages = msgs
                .slice(0, idx)
                .map((m) => ({ role: m.role, content: m.content }));

            // DB keep = user messages before edit × 2 (each turn = user + assistant row)
            const dbKeepCount = msgs.slice(0, idx).filter((m) => m.role === "user").length * 2;

            replaceFromMessage(messageId, newContent);
            await truncateMessages(activeId, dbKeepCount);

            const abortCtrl = new AbortController();
            abortRef.current = abortCtrl;
            setIsThinking(true);
            let assistantMsgId: string | null = null;
            let accumulated = "";
            try {
                const { response, traceId } = await askSeraphim(
                    newContent,
                    activeId,
                    (token) => {
                        accumulated += token;
                        if (assistantMsgId === null) {
                            assistantMsgId = addMessage(accumulated, "assistant", "streaming");
                        } else {
                            updateMessage(assistantMsgId, accumulated, "streaming");
                        }
                    },
                    (sentence) => speakRef.current?.(sentence),
                    engineId,
                    agentId,
                    undefined,
                    contextMessages,
                    abortCtrl.signal,
                );
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

    async function handleSend() {
        await sendMessage(input);
    }

    return (
        <OrbScreen
            conversation={active}
            conversations={conversations}
            activeId={activeId}
            isListening={isListening}
            isThinking={isThinking}
            isSpeaking={isSpeaking}
            input={input}
            onInputChange={setInput}
            onSend={handleSend}
            onVoiceToggle={toggleListening}
            onStopSpeaking={stopSpeaking}
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
        />
    );
}

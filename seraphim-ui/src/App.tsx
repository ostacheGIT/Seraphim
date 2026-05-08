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
    } = useConversation();

    const speakRef = useRef<((text: string) => Promise<void>) | null>(null);

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
            setIsThinking(true);
            try {
                const { response, traceId } = await askSeraphim(
                    trimmed || "Analyse cette image.",
                    activeId ?? undefined,
                    undefined,
                    (sentence) => speakRef.current?.(sentence),
                    engineId,
                    agentId,
                    imageSnapshot ?? undefined,
                );
                addMessage(response, "assistant", "done", traceId ?? undefined);
            } catch {
                const errMsg = "Erreur : impossible de contacter le backend Seraphim.";
                addMessage(errMsg, "assistant", "error");
                await speakRef.current?.(errMsg);
            } finally {
                setIsThinking(false);
            }
        },
        [isThinking, addMessage, activeId, engineId, agentId, pendingImage],
    );

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
            onSelectConversation={setActiveId}
            onNewConversation={newConversation}
            onDeleteConversation={deleteConversation}
            engineId={engineId}
            onEngineChange={setEngineId}
            agentId={agentId}
            onAgentChange={setAgentId}
            pendingImage={pendingImage}
            onImageChange={setPendingImage}
        />
    );
}

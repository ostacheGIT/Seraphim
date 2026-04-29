import { useState, useCallback, useRef } from "react";
import { useConversation } from "./hooks/useConversation";
import { useSpeech } from "./hooks/useSpeech";
import { askSeraphim } from "./hooks/useSeraphimBackend";
import OrbScreen from "./components/OrbScreen";

export default function App() {
  const [input, setInput] = useState("");
  const [isThinking, setIsThinking] = useState(false);

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
        if (!trimmed || isThinking) return;
        setInput("");
        addMessage(trimmed, "user");
        setIsThinking(true);

        try {
          const response = await askSeraphim(
              trimmed,
              activeId ?? undefined, // session_id
              undefined,
              (sentence) => speakRef.current?.(sentence),
              engineId, // <── moteur choisi
          );
          addMessage(response, "assistant", "done");
        } catch {
          const errMsg = "Erreur : impossible de contacter le backend Seraphim.";
          addMessage(errMsg, "assistant", "error");
          await speakRef.current?.(errMsg);
        } finally {
          setIsThinking(false);
        }
      },
      [isThinking, addMessage, activeId, engineId],
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
      />
  );
}
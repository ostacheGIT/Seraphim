import { useState, useCallback } from "react";
import { useConversation } from "./hooks/useConversation";
import { useSpeech } from "./hooks/useSpeech";
import { askSeraphim } from "./hooks/useSeraphimBackend";
import OrbScreen from "./components/OrbScreen";

export default function App() {
  const [input, setInput]         = useState("");
  const [isThinking, setIsThinking] = useState(false);

  const {
    conversations,
    activeId,
    active,
    setActiveId,
    newConversation,
    deleteConversation,
    addMessage,
  } = useConversation();

  // ── Envoi d'un message (texte ou vocal) ──────────────────────────────────
  const sendMessage = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || isThinking) return;

    setInput("");
    addMessage(trimmed, "user");
    setIsThinking(true);

    try {
      const response = await askSeraphim(trimmed);
      addMessage(response, "assistant", "done");
      // 🔊 Seraphim répond à voix haute
      await speak(response);
    } catch {
      const errMsg = "Erreur : impossible de contacter le backend Seraphim.";
      addMessage(errMsg, "assistant", "error");
      await speak(errMsg);
    } finally {
      setIsThinking(false);
    }
    // speak est stable (useCallback dans useSpeech), on l'ajoute après init
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isThinking, addMessage]);

  // ── Speech (STT + TTS) ───────────────────────────────────────────────────
  const {
    isListening,
    isSpeaking,
    toggleListening,
    speak,
    stopSpeaking,
  } = useSpeech({
    lang: "fr-FR",
    rate: 1,
    pitch: 1,
    onTranscript: (transcript) => sendMessage(transcript),
    onError: (err) => console.error("Speech error:", err),
  });

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
      />
  );
}
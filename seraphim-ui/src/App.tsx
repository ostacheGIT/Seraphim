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
    setActiveId,
    newConversation,
    deleteConversation,
    addMessage,
  } = useConversation();

  // ── Ref stable vers speak pour éviter la dépendance circulaire ───────────
  const speakRef = useRef<((text: string) => Promise<void>) | null>(null);

  // ── Speech (STT + TTS) ───────────────────────────────────────────────────
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

  // Garder speakRef à jour à chaque render
  speakRef.current = speak;

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
      // 🔊 Seraphim répond à voix haute via Piper JARVIS
      await speakRef.current?.(response);
    } catch {
      const errMsg = "Erreur : impossible de contacter le backend Seraphim.";
      addMessage(errMsg, "assistant", "error");
      await speakRef.current?.(errMsg);
    } finally {
      setIsThinking(false);
    }
  }, [isThinking, addMessage]);

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
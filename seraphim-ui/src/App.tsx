import { useState, useCallback } from "react";
import { useConversation } from "./hooks/useConversation";
import { useVoice } from "./hooks/useVoice";
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

  const sendMessage = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || isThinking) return;
    setInput("");
    addMessage(trimmed, "user");
    setIsThinking(true);
    try {
      const response = await askSeraphim(trimmed);
      addMessage(response, "assistant", "done");
    } catch {
      addMessage(
          "Erreur : impossible de contacter le backend Seraphim.",
          "assistant",
          "error"
      );
    } finally {
      setIsThinking(false);
    }
  }, [isThinking, addMessage]);

  const { isListening, toggle: toggleVoice } = useVoice({
    onResult: (transcript) => {
      sendMessage(transcript);
    },
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
          input={input}
          onInputChange={setInput}
          onSend={handleSend}
          onVoiceToggle={toggleVoice}
          onSelectConversation={setActiveId}
          onNewConversation={newConversation}
          onDeleteConversation={deleteConversation}
      />
  );
}

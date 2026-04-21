import { useState, useCallback } from "react";
import TitleBar from "./components/TitleBar";
import Sidebar from "./components/Sidebar";
import ChatWindow from "./components/ChatWindow";
import { useConversation } from "./hooks/useConversation";
import { useVoice } from "./hooks/useVoice";
import { askSeraphim } from "./hooks/useSeraphimBackend";

export default function App() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
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
      <div className="app-root" data-theme={theme}>
        <TitleBar
            theme={theme}
            onThemeToggle={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
        />
        <div className="app-layout">
          <Sidebar
              conversations={conversations}
              activeId={activeId}
              onSelect={setActiveId}
              onNew={newConversation}
              onDelete={deleteConversation}
          />
          <ChatWindow
              conversation={active}
              isListening={isListening}
              isThinking={isThinking}
              input={input}
              onInputChange={setInput}
              onSend={handleSend}
              onVoiceToggle={toggleVoice}
          />
        </div>
      </div>
  );
}
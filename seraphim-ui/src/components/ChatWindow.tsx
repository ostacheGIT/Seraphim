import { useEffect, useRef } from "react";
import { ChevronDown } from "lucide-react";
import MessageBubble from "./MessageBubble";
import VoiceButton from "./VoiceButton";
import InputBar from "./InputBar";
import StatusBar from "./StatusBar";
import { Conversation } from "../types";

interface ChatWindowProps {
  conversation: Conversation | null;
  isListening: boolean;
  isThinking: boolean;
  input: string;
  onInputChange: (v: string) => void;
  onSend: () => void;
  onVoiceToggle: () => void;
}

const WAVE_COUNT = 30;

export default function ChatWindow({
  conversation,
  isListening,
  isThinking,
  input,
  onInputChange,
  onSend,
  onVoiceToggle,
}: ChatWindowProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [conversation?.messages]);

  return (
    <main className="main-panel">
      {/* Header */}
      <header className="chat-header">
        <div className="chat-title">
          {conversation?.title ?? "Nouvelle conversation"}
        </div>
        <div className="chat-header-right">
          <button className="model-chip">
            llama3.2 · local
            <ChevronDown size={13} />
          </button>
          <StatusBar isListening={isListening} isThinking={isThinking} />
        </div>
      </header>

      {/* Voice panel */}
      <section className="voice-panel">
        <div className="voice-panel-top">
          <span className="voice-label">Commande vocale</span>
        </div>
        <div className="voice-center">
          <VoiceButton isListening={isListening} onClick={onVoiceToggle} />
          <div className="wave-row" aria-hidden="true">
            {Array.from({ length: WAVE_COUNT }).map((_, i) => (
              <span
                key={i}
                className={`wave-bar ${isListening ? "animated" : ""}`}
                style={{ animationDelay: `${i * 0.04}s` }}
              />
            ))}
          </div>
        </div>
        <p className="voice-hint">Cliquez sur le micro ou parlez...</p>
      </section>

      {/* Messages */}
      <section className="messages">
        {conversation?.messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}

        {/* Thinking indicator */}
        {isThinking && (
          <div className="message-row assistant">
            <div className="avatar assistant-avatar">S</div>
            <div className="bubble assistant-bubble thinking">
              <span className="dot" />
              <span className="dot" />
              <span className="dot" />
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </section>

      {/* Composer */}
      <InputBar
        value={input}
        onChange={onInputChange}
        onSend={onSend}
        onVoice={onVoiceToggle}
        isListening={isListening}
        disabled={isThinking}
      />
    </main>
  );
}
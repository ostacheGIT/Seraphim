import { useRef } from "react";
import { SendHorizontal, Mic } from "lucide-react";

interface InputBarProps {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onVoice: () => void;
  isListening: boolean;
  disabled?: boolean;
}

export default function InputBar({
                                   value,
                                   onChange,
                                   onSend,
                                   onVoice,
                                   isListening,
                                   disabled,
                                 }: InputBarProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  function handleKey(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  }

  return (
      <footer className="composer">
        <div className="composer-box">
          <button
              className={`composer-mic ${isListening ? "active" : ""}`}
              onClick={onVoice}
              aria-label="Commande vocale"
              type="button"
          >
            <Mic size={17} />
          </button>

          <input
              ref={inputRef}
              type="text"
              value={value}
              onChange={(e) => onChange(e.target.value)}
              onKeyDown={handleKey}
              placeholder="Écris une commande pour Seraphim..."
              disabled={disabled}
              autoComplete="off"
          />

          <button
              className="send-btn"
              onClick={onSend}
              disabled={!value.trim() || disabled}
              aria-label="Envoyer"
              type="button"
          >
            <SendHorizontal size={17} />
          </button>
        </div>
      </footer>
  );
}
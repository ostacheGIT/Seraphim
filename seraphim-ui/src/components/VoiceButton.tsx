import { Mic, MicOff } from "lucide-react";

interface VoiceButtonProps {
  isListening: boolean;
  onClick: () => void;
}

export default function VoiceButton({ isListening, onClick }: VoiceButtonProps) {
  return (
      <button
          className={`mic-button ${isListening ? "active" : ""}`}
          onClick={onClick}
          aria-label={isListening ? "Arrêter l'écoute" : "Démarrer l'écoute"}
          type="button"
      >
        {isListening ? <MicOff size={22} /> : <Mic size={22} />}
      </button>
  );
}
interface VoiceButtonProps {
  isListening: boolean;
  onClick: () => void;
}

export default function VoiceButton({ isListening, onClick }: VoiceButtonProps) {
  return (
      <div className="orb-system">
        <div className="orb-ring orb-ring-1" />
        <div className="orb-ring orb-ring-2" />
        <div className="orb-ring orb-ring-3" />

        <button
            className={`mic-button ${isListening ? "active" : ""}`}
            onClick={onClick}
            aria-label={isListening ? "Arrêter l'écoute" : "Démarrer l'écoute"}
            type="button"
        >
          {isListening ? (
              <div className="orb-label">···</div>
          ) : (
              <div className="orb-label">
                SERA
                <br />
                PHIM
              </div>
          )}
        </button>
      </div>
  );
}

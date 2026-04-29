interface StatusBarProps {
  isListening: boolean;
  isThinking: boolean;
}

export default function StatusBar({ isListening, isThinking }: StatusBarProps) {
  const label = isThinking
      ? "Traitement..."
      : isListening
          ? "Écoute..."
          : "En attente";

  const state = isThinking ? "thinking" : isListening ? "live" : "";

  return (
      <div className={`status-pill ${state}`}>
        <span className="status-dot" />
        {label}
      </div>
  );
}
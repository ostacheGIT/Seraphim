import { Minus, Square, X, Sun, Moon } from "lucide-react";

interface TitleBarProps {
  theme: "dark" | "light";
  onThemeToggle: () => void;
}

export default function TitleBar({ theme, onThemeToggle }: TitleBarProps) {
  return (
    <div className="titlebar" data-tauri-drag-region>
      <div className="titlebar-left">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
          <polygon
            points="12,3 22,21 2,21"
            stroke="var(--accent)"
            strokeWidth="2"
            strokeLinejoin="round"
            fill="var(--accent-soft)"
          />
          <circle cx="12" cy="14" r="2.2" fill="var(--accent)" />
        </svg>
        <span className="titlebar-name">Seraphim</span>
      </div>

      <div className="titlebar-right">
        <button className="titlebar-btn" onClick={onThemeToggle} aria-label="Toggle theme">
          {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
        </button>
        <button className="titlebar-btn" aria-label="Minimize">
          <Minus size={13} />
        </button>
        <button className="titlebar-btn" aria-label="Maximize">
          <Square size={11} />
        </button>
        <button className="titlebar-btn close" aria-label="Close">
          <X size={13} />
        </button>
      </div>
    </div>
  );
}
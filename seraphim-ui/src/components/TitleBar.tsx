import { Minus, Square, X, Sun, Moon } from "lucide-react";

interface TitleBarProps {
  theme: "dark" | "light";
  onThemeToggle: () => void;
}

export default function TitleBar({ theme, onThemeToggle }: TitleBarProps) {
  return (
      <div className="titlebar" data-tauri-drag-region>
        <div className="titlebar-left">
          {/* Mini orb icon */}
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="7" stroke="var(--teal)" strokeWidth="1" opacity="0.5"/>
            <circle cx="8" cy="8" r="3" fill="var(--teal)" opacity="0.7"/>
          </svg>
          <span className="titlebar-name">Seraphim</span>
        </div>

        <div className="titlebar-right">
          <button className="titlebar-btn" onClick={onThemeToggle} aria-label="Toggle theme">
            {theme === "dark" ? <Sun size={13} /> : <Moon size={13} />}
          </button>
          <button className="titlebar-btn" aria-label="Minimize">
            <Minus size={12} />
          </button>
          <button className="titlebar-btn" aria-label="Maximize">
            <Square size={10} />
          </button>
          <button className="titlebar-btn close" aria-label="Close">
            <X size={12} />
          </button>
        </div>
      </div>
  );
}

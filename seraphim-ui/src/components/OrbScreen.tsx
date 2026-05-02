import { useState, useRef, useEffect, KeyboardEvent } from "react";
import { Plus, Trash2, VolumeX } from "lucide-react";
import { Conversation } from "../types";
import type { EngineId } from "../hooks/useConversation";
import MessageBubble from "./MessageBubble";
import SphereGL from "./SphereGL";

interface OrbScreenProps {
    conversation: Conversation | null;
    conversations: Conversation[];
    activeId: string | null;
    isListening: boolean;
    isThinking: boolean;
    isSpeaking: boolean;
    input: string;
    onInputChange: (v: string) => void;
    onSend: () => void;
    onVoiceToggle: () => void;
    onStopSpeaking: () => void;
    onSelectConversation: (id: string) => void;
    onNewConversation: () => void;
    onDeleteConversation: (id: string) => void;
    engineId: EngineId;
    onEngineChange: (id: EngineId) => void;
}

export default function OrbScreen({
                                      conversation,
                                      conversations,
                                      activeId,
                                      isListening,
                                      isThinking,
                                      isSpeaking,
                                      input,
                                      onInputChange,
                                      onSend,
                                      onVoiceToggle,
                                      onStopSpeaking,
                                      onSelectConversation,
                                      onNewConversation,
                                      onDeleteConversation,
                                      engineId,
                                      onEngineChange,
                                  }: OrbScreenProps) {
    const [panelOpen, setPanelOpen] = useState(false);
    const [panelWidth, setPanelWidth] = useState(340);
    const [resizing, setResizing] = useState(false);
    const chatBottomRef = useRef<HTMLDivElement>(null);

    const handleResizeStart = (e: React.MouseEvent) => {
        e.preventDefault();
        const startX = e.clientX;
        const startW = panelWidth;
        setResizing(true);

        const onMove = (ev: MouseEvent) => {
            const next = Math.min(800, Math.max(220, startW + (ev.clientX - startX)));
            setPanelWidth(next);
        };
        const onUp = () => {
            setResizing(false);
            document.removeEventListener("mousemove", onMove);
            document.removeEventListener("mouseup", onUp);
        };
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
    };

    useEffect(() => {
        chatBottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [conversation?.messages]);

    const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
        if (e.key === "Enter") onSend();
    };

    const orbState = isListening
        ? "listening"
        : isSpeaking
            ? "speaking"
            : isThinking
                ? "thinking"
                : "idle";

    const statusText = isListening
        ? "● écoute en cours..."
        : isSpeaking
            ? "◈ Seraphim parle..."
            : isThinking
                ? "◌ traitement..."
                : "cliquez pour parler";

    return (
        <div className="orb-root" style={{ ["--panel-w" as string]: `${panelWidth}px` }}>
            {/* Hamburger */}
            <button
                className={`hamburger ${panelOpen ? "open" : ""}`}
                onClick={() => setPanelOpen((p) => !p)}
                aria-label="Menu"
            >
                <span />
                <span />
                <span />
            </button>

            {/* Slide-in chat panel */}
            <aside
                className={`chat-panel ${panelOpen ? "open" : ""}`}
                style={resizing ? { transition: "none" } : undefined}
            >
                <div className="panel-header">
                    <span className="panel-title">CONVERSATIONS</span>
                    <button
                        className="new-chat-btn"
                        onClick={onNewConversation}
                        aria-label="Nouvelle"
                    >
                        <Plus size={14} />
                    </button>
                </div>

                {/* Moteur */}
                <div className="engine-block">
                    <div className="engine-header">
                        <span className="section-label">Moteur</span>
                    </div>
                    <select
                        className="engine-select"
                        value={engineId}
                        onChange={(e) => onEngineChange(e.target.value as EngineId)}
                    >
                        <option value="ollama_qwen3b">Qwen 2.5 3B (rapide)</option>
                        <option value="ollama_qwen7b">Qwen 2.5 7B (précis)</option>
                    </select>
                </div>

                {/* Skill — lecture seule */}
                <div className="engine-block">
                    <div className="engine-header">
                        <span className="section-label">Agent / Skill</span>
                    </div>
                    <div style={{
                        padding: "6px 10px",
                        borderRadius: "6px",
                        background: "rgba(167,139,250,0.08)",
                        border: "1px solid rgba(167,139,250,0.2)",
                        fontSize: "12px",
                        color: "var(--accent, #a78bfa)",
                        display: "flex",
                        alignItems: "center",
                        gap: "6px",
                    }}>
                        <span style={{ opacity: 0.6, fontSize: "10px" }}>⚡</span>
                        Sélection automatique
                        <span style={{
                            marginLeft: "auto",
                            fontSize: "10px",
                            opacity: 0.5,
                            fontStyle: "italic",
                        }}>
                            auto
                        </span>
                    </div>
                </div>

                {/* Liste conversations */}
                <div className="conversation-list">
                    {conversations.length === 0 && (
                        <p className="empty-hint">Aucune conversation</p>
                    )}
                    {conversations.map((c) => (
                        <div
                            key={c.id}
                            className={`conv-item ${activeId === c.id ? "active" : ""}`}
                        >
                            <button
                                className="conv-title"
                                onClick={() => onSelectConversation(c.id)}
                            >
                                {c.title}
                            </button>
                            <button
                                className="conv-delete"
                                onClick={(e) => {
                                    e.stopPropagation();
                                    onDeleteConversation(c.id);
                                }}
                                aria-label="Supprimer"
                            >
                                <Trash2 size={12} />
                            </button>
                        </div>
                    ))}
                </div>

                {/* Messages */}
                <div className="chat-messages">
                    {conversation?.messages.map((msg) => (
                        <MessageBubble key={msg.id} message={msg} />
                    ))}
                    {isThinking && (
                        <div className="chat-msg assistant">
                            <div className="msg-role">SERAPHIM</div>
                            <div className="msg-content thinking-dots">
                                <span />
                                <span />
                                <span />
                            </div>
                        </div>
                    )}
                    <div ref={chatBottomRef} />
                </div>

                {/* Input texte */}
                <div className="chat-input-area">
                    <input
                        type="text"
                        value={input}
                        onChange={(e) => onInputChange(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder="Tapez un message..."
                        className="chat-input"
                    />
                </div>

                {/* Resize handle */}
                <div
                    className={`panel-resize-handle${resizing ? " dragging" : ""}`}
                    onMouseDown={handleResizeStart}
                    aria-hidden
                />
            </aside>

            {/* Orbe principal — Three.js WebGL */}
            <div className={`orb-stage ${panelOpen ? "shifted" : ""}`}>
                <SphereGL
                    state={orbState}
                    onClick={() => {
                        if (!activeId) onNewConversation();
                        onVoiceToggle();
                    }}
                />

                <div className="orb-status">{statusText}</div>

                {isSpeaking && (
                    <button
                        className="mute-btn"
                        onClick={onStopSpeaking}
                        aria-label="Couper la voix"
                    >
                        <VolumeX size={14} />
                        <span>couper</span>
                    </button>
                )}

                <div className="dot-row">
                    <span className={`dot ${orbState === "idle"      ? "active" : ""}`} />
                    <span className={`dot ${orbState === "listening" ? "active" : ""}`} />
                    <span className={`dot ${orbState === "thinking"  ? "active" : ""}`} />
                    <span className={`dot ${orbState === "speaking"  ? "active" : ""}`} />
                </div>
            </div>
        </div>
    );
}
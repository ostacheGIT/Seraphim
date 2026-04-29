import { useState, useRef, useEffect, KeyboardEvent } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Conversation } from "../types";

interface OrbScreenProps {
    conversation: Conversation | null;
    conversations: Conversation[];
    activeId: string | null;
    isListening: boolean;
    isThinking: boolean;
    input: string;
    onInputChange: (v: string) => void;
    onSend: () => void;
    onVoiceToggle: () => void;
    onSelectConversation: (id: string) => void;
    onNewConversation: () => void;
    onDeleteConversation: (id: string) => void;
}

export default function OrbScreen({
                                      conversation,
                                      conversations,
                                      activeId,
                                      isListening,
                                      isThinking,
                                      input,
                                      onInputChange,
                                      onSend,
                                      onVoiceToggle,
                                      onSelectConversation,
                                      onNewConversation,
                                      onDeleteConversation,
                                  }: OrbScreenProps) {
    const [panelOpen, setPanelOpen] = useState(false);
    const chatBottomRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        chatBottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [conversation?.messages]);

    const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
        if (e.key === "Enter") onSend();
    };

    const orbState = isListening ? "listening" : isThinking ? "thinking" : "idle";

    return (
        <div className="orb-root">
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
            <aside className={`chat-panel ${panelOpen ? "open" : ""}`}>
                <div className="panel-header">
                    <span className="panel-title">CONVERSATIONS</span>
                    <button className="new-chat-btn" onClick={onNewConversation} aria-label="Nouvelle conversation">
                        <Plus size={14} />
                    </button>
                </div>

                <div className="conversation-list">
                    {conversations.length === 0 && (
                        <p className="empty-hint">Aucune conversation</p>
                    )}
                    {conversations.map((c) => (
                        <div
                            key={c.id}
                            className={`conv-item ${activeId === c.id ? "active" : ""}`}
                        >
                            <button className="conv-title" onClick={() => onSelectConversation(c.id)}>
                                {c.title}
                            </button>
                            <button
                                className="conv-delete"
                                onClick={(e) => { e.stopPropagation(); onDeleteConversation(c.id); }}
                                aria-label="Supprimer"
                            >
                                <Trash2 size={12} />
                            </button>
                        </div>
                    ))}
                </div>

                {/* Chat messages */}
                <div className="chat-messages">
                    {conversation?.messages.map((msg) => (
                        <div key={msg.id} className={`chat-msg ${msg.role}`}>
                            <div className="msg-role">{msg.role === "user" ? "VOUS" : "SERAPHIM"}</div>
                            <div className="msg-content">{msg.content}</div>
                        </div>
                    ))}
                    {isThinking && (
                        <div className="chat-msg assistant">
                            <div className="msg-role">SERAPHIM</div>
                            <div className="msg-content thinking-dots">
                                <span /><span /><span />
                            </div>
                        </div>
                    )}
                    <div ref={chatBottomRef} />
                </div>

                {/* Text input */}
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
            </aside>

            {/* Backdrop */}
            {panelOpen && <div className="panel-backdrop" onClick={() => setPanelOpen(false)} />}

            {/* Main orb */}
            <div className={`orb-stage ${panelOpen ? "shifted" : ""}`}>
                <div className="orb-wrapper">
                    {/* Rings */}
                    <div className="ring ring-1" />
                    <div className="ring ring-2" />
                    <div className="ring ring-3" />

                    {/* Pulse rings when active */}
                    {(isListening || isThinking) && (
                        <>
                            <div className="pulse-ring pulse-ring-1" />
                            <div className="pulse-ring pulse-ring-2" />
                        </>
                    )}

                    {/* Core */}
                    <div role="button" tabIndex={0}
                         className={`orb-core orb-${orbState}`}
                         onClick={onVoiceToggle}
                         aria-label={isListening ? "Arrêter l'écoute" : "Démarrer l'écoute"}
                    >
                        <div className="orb-inner-glow" />
                        <span className="orb-label">S.E.R.A<br />P.H.I.M</span>
                    </div>
                </div>

                {/* Status */}
                <div className="orb-status">
                    {isListening
                        ? "● écoute en cours..."
                        : isThinking
                            ? "◌ traitement..."
                            : "cliquez pour parler"}
                </div>

                {/* Dot row */}
                <div className="dot-row">
                    <span className={`dot ${orbState === "idle" ? "active" : ""}`} />
                    <span className={`dot ${orbState === "listening" ? "active" : ""}`} />
                    <span className={`dot ${orbState === "thinking" ? "active" : ""}`} />
                </div>
            </div>
        </div>
    );
}

import { useState, useRef, useEffect, KeyboardEvent } from "react";
import { Plus, Trash2, VolumeX } from "lucide-react";
import { Conversation } from "../types";
import type { EngineId } from "../hooks/useConversation";
import { fetchInstalledSkills, InstalledSkill } from "../hooks/useSeraphimBackend";

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
    selectedSkill: string;
    onSkillChange: (skill: string) => void;
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
                                      selectedSkill,
                                      onSkillChange,
                                  }: OrbScreenProps) {
    const [panelOpen, setPanelOpen] = useState(false);
    const [installedSkills, setInstalledSkills] = useState<InstalledSkill[]>([]);
    const chatBottomRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        chatBottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [conversation?.messages]);

    useEffect(() => {
        fetchInstalledSkills().then(setInstalledSkills);
    }, []);

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

    const skillLabel = selectedSkill.startsWith("skill:")
        ? `🧠 ${selectedSkill.split(":")[1]}`
        : selectedSkill;

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

                {/* Skills */}
                <div className="engine-block">
                    <div className="engine-header">
                        <span className="section-label">Agent / Skill</span>
                    </div>
                    <select
                        className="engine-select"
                        value={selectedSkill}
                        onChange={(e) => onSkillChange(e.target.value)}
                    >
                        <optgroup label="Agents">
                            <option value="chat">💬 Chat</option>
                            <option value="react">⚙️ ReAct</option>
                            <option value="coder">👨‍💻 Coder</option>
                            <option value="researcher">🔬 Researcher</option>
                        </optgroup>
                        {installedSkills.length > 0 && (
                            <optgroup label="Skills Hermes">
                                {installedSkills.map((s) => (
                                    <option key={s.id} value={s.id}>
                                        🧠 {s.name}
                                    </option>
                                ))}
                            </optgroup>
                        )}
                    </select>
                </div>

                {/* Indicateur skill actif */}
                {selectedSkill.startsWith("skill:") && (
                    <div style={{
                        padding: "6px 12px",
                        fontSize: "11px",
                        color: "var(--accent, #a78bfa)",
                        borderBottom: "1px solid rgba(255,255,255,0.06)",
                    }}>
                        {skillLabel} actif
                    </div>
                )}

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
                        <div key={msg.id} className={`chat-msg ${msg.role}`}>
                            <div className="msg-role">
                                {msg.role === "user" ? "VOUS" : "SERAPHIM"}
                            </div>
                            <div className="msg-content">{msg.content}</div>
                        </div>
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
            </aside>

            {/* Orbe principal */}
            <div className={`orb-stage ${panelOpen ? "shifted" : ""}`}>
                <div className="orb-wrapper">
                    <div className="ring ring-1" />
                    <div className="ring ring-2" />
                    <div className="ring ring-3" />

                    {(isListening || isThinking || isSpeaking) && (
                        <>
                            <div className={`pulse-ring pulse-ring-1 pulse-${orbState}`} />
                            <div className={`pulse-ring pulse-ring-2 pulse-${orbState}`} />
                        </>
                    )}

                    <div
                        className={`orb-core orb-${orbState}`}
                        role="button"
                        tabIndex={0}
                        onClick={() => {
                            if (!activeId) onNewConversation();
                            onVoiceToggle();
                        }}
                        onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                                if (!activeId) onNewConversation();
                                onVoiceToggle();
                            }
                        }}
                        aria-label={isListening ? "Arrêter l'écoute" : "Démarrer l'écoute"}
                    >
                        <div className="orb-inner-glow" />
                        <span className="orb-label">
                            S.E.R.A
                            <br />
                            P.H.I.M
                        </span>
                    </div>
                </div>

                {/* Status + skill actif sous l'orbe */}
                <div className="orb-status">{statusText}</div>
                {selectedSkill.startsWith("skill:") && (
                    <div style={{
                        fontSize: "11px",
                        color: "var(--accent, #a78bfa)",
                        opacity: 0.8,
                        marginTop: "4px",
                    }}>
                        {skillLabel}
                    </div>
                )}

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
                    <span className={`dot ${orbState === "idle" ? "active" : ""}`} />
                    <span className={`dot ${orbState === "listening" ? "active" : ""}`} />
                    <span className={`dot ${orbState === "thinking" ? "active" : ""}`} />
                    <span className={`dot ${orbState === "speaking" ? "active" : ""}`} />
                </div>
            </div>
        </div>
    );
}
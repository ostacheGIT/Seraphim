import { useState, useRef, useEffect, KeyboardEvent } from "react";
import { Plus, Trash2, BookOpen, VolumeX } from "lucide-react";
import { Conversation } from "../types";
import type { EngineId } from "../hooks/useConversation";
import MessageBubble from "./MessageBubble";
import SphereGL from "./SphereGL";
import SkillCatalogPanel from "./SkillCatalogPanel";
import { fetchInstalledSkills } from "../hooks/useSeraphimBackend";

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
    agentId: string;
    onAgentChange: (id: string) => void;
}

const BASE_AGENTS = [
    { id: "auto",                   label: "⚡ Auto" },
    { id: "chat",                   label: "💬 Chat" },
    { id: "react",                  label: "⚙️ Système" },
    { id: "skill:calculator",       label: "🔢 Calculatrice" },
    { id: "skill:web_search",       label: "🌐 Web Search" },
    { id: "skill:code_interpreter", label: "🐍 Code" },
    { id: "skill:think",            label: "🧠 Raisonnement" },
];

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
    agentId,
    onAgentChange,
}: OrbScreenProps) {
    const [panelOpen, setPanelOpen]       = useState(false);
    const [catalogOpen, setCatalogOpen]   = useState(false);
    const [panelWidth, setPanelWidth]     = useState(340);
    const [catalogWidth, setCatalogWidth] = useState(340);
    const [resizing, setResizing]         = useState(false);
    const [catalogResizing, setCatalogResizing] = useState(false);
    const [view, setView]                 = useState<"list" | "chat">("list");
    const [installedSkillAgents, setInstalledSkillAgents] = useState<{ id: string; label: string }[]>([]);
    const chatBottomRef = useRef<HTMLDivElement>(null);

    const refreshInstalledSkills = () => {
        fetchInstalledSkills().then((skills) => {
            const baseIds = new Set(BASE_AGENTS.map((a) => a.id));
            const extra = skills
                .filter((s) => !baseIds.has(s.id))
                .map((s) => ({ id: s.id, label: `🔧 ${s.name}` }));
            setInstalledSkillAgents(extra);
        });
    };

    useEffect(() => { refreshInstalledSkills(); }, []);

    const agents = [...BASE_AGENTS, ...installedSkillAgents];

    const handleSelectConversation = (id: string) => {
        onSelectConversation(id);
        setView("chat");
    };

    const handleNewConversation = () => {
        onNewConversation();
        setView("chat");
    };

    const handleResizeStart = (e: React.MouseEvent) => {
        e.preventDefault();
        const startX = e.clientX;
        const startW = panelWidth;
        setResizing(true);
        const onMove = (ev: MouseEvent) => {
            setPanelWidth(Math.min(800, Math.max(220, startW + (ev.clientX - startX))));
        };
        const onUp = () => {
            setResizing(false);
            document.removeEventListener("mousemove", onMove);
            document.removeEventListener("mouseup", onUp);
        };
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
    };

    const handleCatalogResizeStart = (e: React.MouseEvent) => {
        e.preventDefault();
        const startX = e.clientX;
        const startW = catalogWidth;
        setCatalogResizing(true);
        const onMove = (ev: MouseEvent) => {
            // dragging left edge: move left = grow, move right = shrink
            setCatalogWidth(Math.min(800, Math.max(220, startW - (ev.clientX - startX))));
        };
        const onUp = () => {
            setCatalogResizing(false);
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

    const orbState = isListening ? "listening"
        : isSpeaking ? "speaking"
        : isThinking  ? "thinking"
        : "idle";

    const statusText = isListening ? "● écoute en cours..."
        : isSpeaking ? "◈ Seraphim parle..."
        : isThinking  ? "◌ traitement..."
        : "cliquez pour parler";

    const orbShift = panelOpen && !catalogOpen ? "shifted"
        : catalogOpen && !panelOpen ? "shifted-right"
        : panelOpen && catalogOpen  ? "shifted-both"
        : "";

    return (
        <div className="orb-root" style={{ ["--panel-w" as string]: `${panelWidth}px`, ["--catalog-w" as string]: `${catalogWidth}px` }}>

            {/* ── Hamburger (gauche) ─────────────────────────────────── */}
            <button
                className={`hamburger ${panelOpen ? "open" : ""}`}
                onClick={() => setPanelOpen((p) => !p)}
                aria-label="Menu"
            >
                <span /><span /><span />
            </button>

            {/* ── Bouton catalogue (droite) ──────────────────────────── */}
            <button
                className={`catalog-toggle-btn ${catalogOpen ? "open" : ""}`}
                onClick={() => setCatalogOpen((p) => !p)}
                aria-label="Catalogue Skills"
                title="Catalogue de skills"
            >
                <BookOpen size={14} />
            </button>

            {/* ── Panel gauche — conversations ───────────────────────── */}
            <aside
                className={`chat-panel ${panelOpen ? "open" : ""}`}
                style={resizing ? { transition: "none" } : undefined}
            >
                <div className="panel-header">
                    {view === "chat" ? (
                        <>
                            <button
                                className="new-chat-btn"
                                onClick={() => setView("list")}
                                aria-label="Retour à la liste"
                                style={{ fontSize: "18px", fontWeight: 300, lineHeight: 1, padding: "2px 6px" }}
                            >
                                &lt;
                            </button>
                            <span className="panel-title" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {conversation?.title ?? "Conversation"}
                            </span>
                        </>
                    ) : (
                        <>
                            <span className="panel-title">CONVERSATIONS</span>
                            <button
                                className="new-chat-btn"
                                onClick={handleNewConversation}
                                aria-label="Nouvelle"
                            >
                                <Plus size={14} />
                            </button>
                        </>
                    )}
                </div>

                {/* Moteur + Agent */}
                <div style={{ display: "flex", gap: "0.5rem", margin: "0.6rem 1rem" }}>
                    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "0.4rem" }}>
                        <span className="section-label">Moteur</span>
                        <select
                            className="engine-select"
                            value={engineId}
                            onChange={(e) => onEngineChange(e.target.value as EngineId)}
                            style={{ width: "100%" }}
                        >
                            <option value="ollama_qwen3b">Qwen 2.5 3B · Rapide</option>
                            <option value="ollama_qwen7b">Qwen 2.5 7B · Précis</option>
                        </select>
                    </div>
                    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "0.4rem" }}>
                        <span className="section-label">Agent</span>
                        <select
                            className="engine-select"
                            value={agentId}
                            onChange={(e) => onAgentChange(e.target.value)}
                            style={{ width: "100%" }}
                        >
                            {agents.map((a) => (
                                <option key={a.id} value={a.id}>{a.label}</option>
                            ))}
                        </select>
                    </div>
                </div>

                {/* Vue liste */}
                {view === "list" && (
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
                                    onClick={() => handleSelectConversation(c.id)}
                                >
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
                )}

                {/* Vue conversation */}
                {view === "chat" && (
                    <>
                        <div className="chat-messages">
                            {conversation?.messages.map((msg) => (
                                <MessageBubble key={msg.id} message={msg} />
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
                    </>
                )}

                <div
                    className={`panel-resize-handle${resizing ? " dragging" : ""}`}
                    onMouseDown={handleResizeStart}
                    aria-hidden
                />
            </aside>

            {/* ── Panel droit — catalogue skills ─────────────────────── */}
            <aside
                className={`catalog-side-panel ${catalogOpen ? "open" : ""}`}
                style={catalogResizing ? { transition: "none" } : undefined}
            >
                <div
                    className={`catalog-resize-handle${catalogResizing ? " dragging" : ""}`}
                    onMouseDown={handleCatalogResizeStart}
                    aria-hidden
                />
                <div className="panel-header">
                    <span className="panel-title">CATALOGUE SKILLS</span>
                </div>
                <SkillCatalogPanel onInstalled={refreshInstalledSkills} />
            </aside>

            {/* ── Orbe principal ─────────────────────────────────────── */}
            <div className={`orb-stage ${orbShift}`}>
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

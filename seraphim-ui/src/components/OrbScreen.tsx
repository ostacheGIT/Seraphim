import { useState, useRef, useEffect, KeyboardEvent } from "react";
import { Plus, Trash2, BookOpen, VolumeX, Sun, Moon, Paperclip, X } from "lucide-react";
import * as pdfjsLib from "pdfjs-dist";
import { Conversation } from "../types";
import type { EngineId } from "../hooks/useConversation";
import type { Theme } from "../hooks/useTheme";
import MessageBubble from "./MessageBubble";
import SphereGL from "./SphereGL";
import SkillCatalogPanel from "./SkillCatalogPanel";
import { fetchInstalledSkills, getRagStatus, ingestToRAG, resetRAG } from "../hooks/useSeraphimBackend";

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
    "pdfjs-dist/build/pdf.worker.mjs",
    import.meta.url,
).href;

interface OrbScreenProps {
    conversation: Conversation | null;
    conversations: Conversation[];
    activeId: string | null;
    isListening: boolean;
    isThinking: boolean;
    isSpeaking: boolean;
    onSend: (text: string) => void;
    onVoiceToggle: () => void;
    onStopSpeaking: () => void;
    onSelectConversation: (id: string) => void;
    onNewConversation: () => void;
    onDeleteConversation: (id: string) => void;
    engineId: EngineId;
    onEngineChange: (id: EngineId) => void;
    agentId: string;
    onAgentChange: (id: string) => void;
    pendingImage?: string | null;
    onImageChange?: (img: string | null) => void;
    pendingFile?: { name: string; content: string } | null;
    onFileChange?: (file: { name: string; content: string } | null) => void;
    theme?: Theme;
    onThemeToggle?: () => void;
    onEditMessage?: (messageId: string, newContent: string) => void;
    onStop?: () => void;
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

const MAX_FILE_CHARS = 24_000; // ~6 000 tokens — fits comfortably in 8192 num_ctx

async function extractFileText(file: File): Promise<{ name: string; content: string }> {
    let content: string;

    if (file.type === "application/pdf" || file.name.endsWith(".pdf")) {
        const buffer = await file.arrayBuffer();
        const pdf = await pdfjsLib.getDocument({ data: buffer }).promise;
        const pages: string[] = [];
        for (let i = 1; i <= Math.min(pdf.numPages, 30); i++) {
            const page = await pdf.getPage(i);
            const textContent = await page.getTextContent();
            pages.push(textContent.items.map((item) => ("str" in item ? item.str : "")).join(" "));
            if (pages.join("\n\n").length > MAX_FILE_CHARS) break;
        }
        content = pages.join("\n\n");
    } else {
        content = await file.text();
    }

    if (content.length > MAX_FILE_CHARS) {
        content = content.slice(0, MAX_FILE_CHARS) + `\n\n[… contenu tronqué à ${MAX_FILE_CHARS} caractères]`;
    }

    return { name: file.name, content };
}

export default function OrbScreen({
    conversation,
    conversations,
    activeId,
    isListening,
    isThinking,
    isSpeaking,
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
    pendingImage,
    onImageChange,
    pendingFile,
    onFileChange,
    theme = "dark",
    onThemeToggle,
    onEditMessage,
    onStop,
}: OrbScreenProps) {
    const [panelOpen, setPanelOpen]       = useState(false);
    const [catalogOpen, setCatalogOpen]   = useState(false);
    const [panelWidth, setPanelWidth]     = useState(340);
    const [catalogWidth, setCatalogWidth] = useState(340);
    const [resizing, setResizing]         = useState(false);
    const [catalogResizing, setCatalogResizing] = useState(false);
    const [view, setView]                 = useState<"list" | "chat">("list");
    const [installedSkillAgents, setInstalledSkillAgents] = useState<{ id: string; label: string }[]>([]);
    const [ragCount, setRagCount]         = useState(0);
    const [ragIngesting, setRagIngesting] = useState(false);
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

    const refreshRagStatus = () => {
        getRagStatus().then((s) => setRagCount(s.doc_count));
    };

    useEffect(() => {
        refreshInstalledSkills();
        refreshRagStatus();
    }, []);

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

    const inputRef = useRef<HTMLInputElement>(null);

    const handleSendInput = () => {
        const text = inputRef.current?.value ?? "";
        if (!text.trim() && !pendingImage) return;
        onSend(text);
        if (inputRef.current) inputRef.current.value = "";
    };

    const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
        if (e.key === "Enter") handleSendInput();
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

            {/* ── Bouton thème (droite, avant catalogue) ─────────────── */}
            <button
                className="theme-toggle-btn"
                onClick={onThemeToggle}
                aria-label={theme === "dark" ? "Mode clair" : "Mode sombre"}
                title={theme === "dark" ? "Mode clair" : "Mode sombre"}
            >
                {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
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

                {/* Base de connaissances (RAG) */}
                <div className="rag-status-bar">
                    <span className="rag-doc-count">
                        KB · {ragCount} fragment{ragCount !== 1 ? "s" : ""}
                    </span>
                    <button
                        className="rag-reset-btn"
                        title="Vider la base de connaissances"
                        onClick={async () => {
                            if (!window.confirm("Vider toute la base de connaissances ?")) return;
                            await resetRAG();
                            refreshRagStatus();
                        }}
                    >
                        Vider
                    </button>
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
                                <MessageBubble
                                    key={msg.id}
                                    message={msg}
                                    onEdit={onEditMessage}
                                    onStop={msg.status === "streaming" ? onStop : undefined}
                                />
                            ))}
                            {isThinking && !conversation?.messages.some(m => m.status === "streaming") && (
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
                            {(pendingImage || pendingFile) && (
                                <div className="input-attachments">
                                    {pendingImage && (
                                        <div className="img-preview-wrap">
                                            <img
                                                src={`data:image/png;base64,${pendingImage}`}
                                                alt="Image à envoyer"
                                                className="img-preview"
                                            />
                                            <button
                                                className="img-preview-remove"
                                                onClick={() => onImageChange?.(null)}
                                                aria-label="Supprimer l'image"
                                            >✕</button>
                                        </div>
                                    )}
                                    {pendingFile && (
                                        <div className="file-preview-wrap">
                                            <Paperclip size={11} />
                                            <span className="file-preview-name">{pendingFile.name}</span>
                                            <button
                                                className="file-kb-btn"
                                                title="Mémoriser dans la base de connaissances"
                                                disabled={ragIngesting}
                                                onClick={async () => {
                                                    if (!pendingFile) return;
                                                    setRagIngesting(true);
                                                    await ingestToRAG(pendingFile.content, pendingFile.name);
                                                    setRagIngesting(false);
                                                    refreshRagStatus();
                                                }}
                                            >
                                                {ragIngesting ? "…" : "KB"}
                                            </button>
                                            <button
                                                className="file-preview-remove"
                                                onClick={() => onFileChange?.(null)}
                                                aria-label="Supprimer le fichier"
                                            >
                                                <X size={10} />
                                            </button>
                                        </div>
                                    )}
                                </div>
                            )}
                            <div className="chat-input-row">
                                <label className="file-attach-btn" title="Joindre un fichier (PDF, TXT, MD…)">
                                    <input
                                        type="file"
                                        accept=".pdf,.txt,.md,.py,.js,.ts,.json,.csv,.xml,.html,.css,.yaml,.yml"
                                        style={{ display: "none" }}
                                        onChange={async (e) => {
                                            const file = e.target.files?.[0];
                                            if (!file) return;
                                            e.target.value = "";
                                            const extracted = await extractFileText(file);
                                            onFileChange?.(extracted);
                                        }}
                                    />
                                    <Paperclip size={13} />
                                </label>
                                <input
                                    ref={inputRef}
                                    type="text"
                                    defaultValue=""
                                    onKeyDown={handleKeyDown}
                                    onPaste={(e) => {
                                        const items = e.clipboardData?.items;
                                        if (!items) return;
                                        for (const item of Array.from(items)) {
                                            if (item.type.startsWith("image/")) {
                                                e.preventDefault();
                                                const file = item.getAsFile();
                                                if (!file) continue;
                                                const reader = new FileReader();
                                                reader.onload = () => {
                                                    const result = reader.result as string;
                                                    onImageChange?.(result.split(",")[1]);
                                                };
                                                reader.readAsDataURL(file);
                                                break;
                                            }
                                        }
                                    }}
                                    onDragOver={(e) => e.preventDefault()}
                                    onDrop={async (e) => {
                                        e.preventDefault();
                                        const files = Array.from(e.dataTransfer.files);
                                        const imgFile = files.find((f) => f.type.startsWith("image/"));
                                        if (imgFile) {
                                            const reader = new FileReader();
                                            reader.onload = () => {
                                                const result = reader.result as string;
                                                onImageChange?.(result.split(",")[1]);
                                            };
                                            reader.readAsDataURL(imgFile);
                                            return;
                                        }
                                        const textFile = files.find(
                                            (f) =>
                                                f.type === "application/pdf" ||
                                                f.type.startsWith("text/") ||
                                                /\.(pdf|txt|md|py|js|ts|json|csv|xml|html|css|yaml|yml)$/i.test(f.name),
                                        );
                                        if (textFile) {
                                            const extracted = await extractFileText(textFile);
                                            onFileChange?.(extracted);
                                        }
                                    }}
                                    placeholder={
                                        pendingImage
                                            ? "Ajoutez un message (optionnel)…"
                                            : pendingFile
                                            ? `${pendingFile.name} joint — posez votre question…`
                                            : "Tapez un message, collez une image ou déposez un fichier…"
                                    }
                                    className="chat-input"
                                />
                            </div>
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
                    theme={theme}
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

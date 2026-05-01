interface SidebarProps {
    sessions: { session_id: string; title: string; agent: string; updated_at: string }[];
    currentSession: string | null;
    routedAgent: string | null;
    onSelectSession: (id: string) => void;
    onNewChat: () => void;
    engineId: string;
    onEngineChange: (id: string) => void;
}

const ENGINES = [
    { id: "ollama_qwen3b", label: "Qwen 2.5 3B · Rapide" },
    { id: "ollama_qwen7b", label: "Qwen 2.5 7B · Précis" },
];

const AGENT_LABELS: Record<string, string> = {
    chat: "💬 Chat",
    react: "⚙️ Système",
    "skill:calculator": "🔢 Calculatrice",
    "skill:web_search": "🌐 Web Search",
    "skill:code_interpreter": "🐍 Code",
    "skill:think": "🧠 Raisonnement",
};

export default function Sidebar({
                                    sessions,
                                    currentSession,
                                    routedAgent,
                                    onSelectSession,
                                    onNewChat,
                                    engineId,
                                    onEngineChange,
                                }: SidebarProps) {
    return (
        <aside
            style={{
                width: 240,
                minHeight: "100vh",
                background: "var(--color-surface, #1c1b19)",
                borderRight: "1px solid var(--color-border, #393836)",
                display: "flex",
                flexDirection: "column",
                padding: "1rem 0.75rem",
                gap: "1.25rem",
            }}
        >
            {/* Header */}
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <span style={{ fontSize: "1.25rem", fontWeight: 700, letterSpacing: "-0.02em" }}>
                    ✦ Seraphim
                </span>
            </div>

            {/* Nouveau chat */}
            <button
                onClick={onNewChat}
                style={{
                    background: "var(--color-primary, #4f98a3)",
                    color: "#fff",
                    border: "none",
                    borderRadius: "0.5rem",
                    padding: "0.5rem 1rem",
                    fontWeight: 600,
                    cursor: "pointer",
                    width: "100%",
                    textAlign: "left",
                }}
            >
                + Nouveau chat
            </button>

            {/* Modèle (seul paramètre réglable) */}
            <div>
                <label
                    style={{
                        fontSize: "0.75rem",
                        textTransform: "uppercase",
                        letterSpacing: "0.08em",
                        color: "var(--color-text-muted, #797876)",
                        marginBottom: "0.35rem",
                        display: "block",
                    }}
                >
                    Modèle
                </label>
                <select
                    value={engineId}
                    onChange={(e) => onEngineChange(e.target.value)}
                    style={{
                        width: "100%",
                        padding: "0.4rem 0.6rem",
                        borderRadius: "0.375rem",
                        border: "1px solid var(--color-border, #393836)",
                        background: "var(--color-surface-2, #201f1d)",
                        color: "var(--color-text, #cdccca)",
                        fontSize: "0.875rem",
                    }}
                >
                    {ENGINES.map((e) => (
                        <option key={e.id} value={e.id}>
                            {e.label}
                        </option>
                    ))}
                </select>
            </div>

            {/* Skill actif (lecture seule — auto-sélectionné) */}
            {routedAgent && (
                <div
                    style={{
                        background: "var(--color-surface-offset, #1d1c1a)",
                        borderRadius: "0.5rem",
                        padding: "0.5rem 0.75rem",
                        fontSize: "0.8rem",
                        color: "var(--color-text-muted, #797876)",
                    }}
                >
                    <span style={{ display: "block", marginBottom: "0.2rem", fontSize: "0.7rem", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                        Skill actif
                    </span>
                    <span style={{ color: "var(--color-primary, #4f98a3)", fontWeight: 600 }}>
                        {AGENT_LABELS[routedAgent] ?? routedAgent}
                    </span>
                    <span style={{ marginLeft: "0.4rem", fontSize: "0.7rem", opacity: 0.6 }}>
                        (auto)
                    </span>
                </div>
            )}

            {/* Historique */}
            <div style={{ flex: 1, overflowY: "auto" }}>
                <label
                    style={{
                        fontSize: "0.75rem",
                        textTransform: "uppercase",
                        letterSpacing: "0.08em",
                        color: "var(--color-text-muted, #797876)",
                        marginBottom: "0.35rem",
                        display: "block",
                    }}
                >
                    Historique
                </label>
                {sessions.length === 0 && (
                    <p style={{ fontSize: "0.8rem", color: "var(--color-text-faint, #5a5957)" }}>
                        Aucune session
                    </p>
                )}
                {sessions.map((s) => (
                    <button
                        key={s.session_id}
                        onClick={() => onSelectSession(s.session_id)}
                        style={{
                            display: "block",
                            width: "100%",
                            textAlign: "left",
                            padding: "0.4rem 0.5rem",
                            borderRadius: "0.375rem",
                            border: "none",
                            background: currentSession === s.session_id
                                ? "var(--color-surface-dynamic, #2d2c2a)"
                                : "transparent",
                            color: "var(--color-text, #cdccca)",
                            fontSize: "0.8rem",
                            cursor: "pointer",
                            marginBottom: "0.2rem",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                        }}
                    >
                        {s.title}
                    </button>
                ))}
            </div>
        </aside>
    );
}
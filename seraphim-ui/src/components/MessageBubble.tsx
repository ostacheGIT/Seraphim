import { useState, useRef, useEffect } from "react";
import { Copy, Check, Terminal, Pencil } from "lucide-react";
import { Message } from "../types";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";
import { sendFeedback } from "../hooks/useSeraphimBackend";

interface MessageBubbleProps {
    message: Message;
    onEdit?: (messageId: string, newContent: string) => void;
}

function CodeBlock({ lang, content }: { lang: string; content: string }) {
    const [copied, setCopied] = useState(false);
    const lineCount = content.split("\n").length;

    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(content);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch { /* silent */ }
    };

    return (
        <div className="code-block">
            <div className="code-block-header">
                <span className="code-block-lang">
                    <Terminal size={12} strokeWidth={2} />
                    {lang || "code"}
                </span>
                <button
                    className={`code-block-copy ${copied ? "copied" : ""}`}
                    onClick={handleCopy}
                    aria-label="Copier"
                >
                    {copied ? <Check size={13} /> : <Copy size={13} />}
                    <span>{copied ? "Copié !" : "Copier"}</span>
                </button>
            </div>
            <SyntaxHighlighter
                language={lang || "text"}
                style={vscDarkPlus}
                showLineNumbers={lineCount > 3}
                wrapLines
                customStyle={{
                    margin: 0,
                    padding: "12px 14px",
                    background: "transparent",
                    fontSize: "11.5px",
                    lineHeight: "1.6",
                    fontFamily: "'Share Tech Mono', 'Fira Code', 'Cascadia Code', monospace",
                }}
                lineNumberStyle={{
                    minWidth: "2.2em",
                    paddingRight: "1em",
                    color: "rgba(255,255,255,0.2)",
                    userSelect: "none",
                    fontSize: "10px",
                }}
            >
                {content}
            </SyntaxHighlighter>
        </div>
    );
}

const RATING_COLORS = ["#f87171", "#fb923c", "#fbbf24", "#a3e635", "#4ade80"];

function FeedbackButtons({ traceId }: { traceId: string }) {
    const [voted, setVoted] = useState<number | null>(null);

    const rate = async (rating: number) => {
        if (voted !== null) return;
        setVoted(rating);
        const score = (rating - 1) / 4;
        try {
            await sendFeedback(traceId, score);
        } catch { /* silent */ }
    };

    return (
        <div className="msg-feedback">
            {voted === null && <span className="feedback-label">Utile ?</span>}
            {[1, 2, 3, 4, 5].map((r) => (
                <button
                    key={r}
                    className={`feedback-btn ${voted === r ? "active" : ""}`}
                    style={voted === r ? { color: RATING_COLORS[r - 1], borderColor: RATING_COLORS[r - 1] } : {}}
                    onClick={() => rate(r)}
                    disabled={voted !== null}
                    aria-label={`Note ${r}/5`}
                >
                    {r}
                </button>
            ))}
            {voted !== null && <span className="feedback-label">Merci !</span>}
        </div>
    );
}

const MD_COMPONENTS = {
    code({ className, children, ...props }: React.ComponentPropsWithoutRef<"code"> & { className?: string }) {
        const match = /language-(\w+)/.exec(className || "");
        const lang = match ? match[1] : "";
        const content = String(children).replace(/\n$/, "");
        const isBlock = !!match || content.includes("\n");
        if (isBlock) return <CodeBlock lang={lang} content={content} />;
        return <code className="md-inline-code" {...props}>{children}</code>;
    },
    table: ({ children }: React.ComponentPropsWithoutRef<"table">) => (
        <div className="md-table-wrap"><table className="md-table">{children}</table></div>
    ),
    pre: ({ children }: React.ComponentPropsWithoutRef<"pre">) => <>{children}</>,
};

export default function MessageBubble({ message, onEdit }: MessageBubbleProps) {
    const isUser = message.role === "user";
    const [isEditing, setIsEditing] = useState(false);
    const [editText, setEditText] = useState(message.content);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    useEffect(() => {
        if (isEditing) {
            textareaRef.current?.focus();
            textareaRef.current?.select();
        }
    }, [isEditing]);

    const handleEditStart = () => {
        setEditText(message.content);
        setIsEditing(true);
    };

    const handleEditSubmit = () => {
        const trimmed = editText.trim();
        if (trimmed && trimmed !== message.content) {
            onEdit?.(message.id, trimmed);
        }
        setIsEditing(false);
    };

    return (
        <div className={`chat-msg ${isUser ? "user" : "assistant"}`}>
            <div className="msg-role">
                {isUser ? "VOUS" : "SERAPHIM"}
                {isUser && onEdit && !isEditing && (
                    <button className="msg-edit-btn" onClick={handleEditStart} aria-label="Modifier">
                        <Pencil size={10} />
                    </button>
                )}
            </div>
            <div className="msg-content md-body">
                {isEditing ? (
                    <div className="msg-edit-area">
                        <textarea
                            ref={textareaRef}
                            className="msg-edit-textarea"
                            value={editText}
                            onChange={(e) => setEditText(e.target.value)}
                            onKeyDown={(e) => {
                                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleEditSubmit(); }
                                if (e.key === "Escape") setIsEditing(false);
                            }}
                            rows={3}
                        />
                        <div className="msg-edit-actions">
                            <button className="msg-edit-cancel" onClick={() => setIsEditing(false)}>Annuler</button>
                            <button className="msg-edit-send" onClick={handleEditSubmit}>Envoyer</button>
                        </div>
                    </div>
                ) : (
                    <>
                        {message.imageUrl && (
                            <img src={message.imageUrl} alt="Image envoyée" className="msg-image" />
                        )}
                        <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                            {message.content}
                        </ReactMarkdown>
                        {!isUser && message.traceId && <FeedbackButtons traceId={message.traceId} />}
                    </>
                )}
            </div>
        </div>
    );
}

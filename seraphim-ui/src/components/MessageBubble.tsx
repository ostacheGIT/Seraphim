import { useState } from "react";
import { Copy, Check, Terminal } from "lucide-react";
import { Message } from "../types";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";

interface MessageBubbleProps {
    message: Message;
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

export default function MessageBubble({ message }: MessageBubbleProps) {
    const isUser = message.role === "user";

    return (
        <div className={`chat-msg ${isUser ? "user" : "assistant"}`}>
            <div className="msg-role">
                {isUser ? "VOUS" : "SERAPHIM"}
            </div>
            <div className="msg-content md-body">
                <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                        // ── Code blocks ───────────────────────────────────────
                        code({ className, children, ...props }) {
                            const match = /language-(\w+)/.exec(className || "");
                            const lang = match ? match[1] : "";
                            const content = String(children).replace(/\n$/, "");
                            // Block code: has a language class OR multi-line
                            const isBlock = !!match || content.includes("\n");
                            if (isBlock) {
                                return <CodeBlock lang={lang} content={content} />;
                            }
                            return (
                                <code className="md-inline-code" {...props}>
                                    {children}
                                </code>
                            );
                        },
                        // ── Tables ────────────────────────────────────────────
                        table: ({ children }) => (
                            <div className="md-table-wrap">
                                <table className="md-table">{children}</table>
                            </div>
                        ),
                        // ── Pre wrapper (react-markdown wraps code in pre) ────
                        pre: ({ children }) => <>{children}</>,
                    }}
                >
                    {message.content}
                </ReactMarkdown>
            </div>
        </div>
    );
}

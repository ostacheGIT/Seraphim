import { useState } from "react";
import { Copy, Check, Terminal } from "lucide-react";
import { Message } from "../types";

interface MessageBubbleProps {
    message: Message;
}

// ─── Types pour les segments parsés ───────────────────────────────────────────
type Segment =
    | { type: "text"; content: string }
    | { type: "code"; lang: string; content: string };

// ─── Parser Markdown minimal : extrait les blocs ```lang\n...\n``` ─────────────
function parseSegments(raw: string): Segment[] {
    const segments: Segment[] = [];
    const regex = /```(\w*)\n?([\s\S]*?)```/g;
    let lastIndex = 0;
    let match: RegExpExecArray | null;

    while ((match = regex.exec(raw)) !== null) {
        if (match.index > lastIndex) {
            const text = raw.slice(lastIndex, match.index).trim();
            if (text) segments.push({ type: "text", content: text });
        }
        segments.push({
            type: "code",
            lang: match[1]?.trim() || "plaintext",
            content: match[2].trimEnd(),
        });
        lastIndex = match.index + match[0].length;
    }

    const remaining = raw.slice(lastIndex).trim();
    if (remaining) segments.push({ type: "text", content: remaining });

    return segments;
}

// ─── Composant bloc de code ───────────────────────────────────────────────────
function CodeBlock({ lang, content }: { lang: string; content: string }) {
    const [copied, setCopied] = useState(false);

    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(content);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch {
            // fallback silencieux
        }
    };

    const lines = content.split("\n");
    const showLineNumbers = lines.length > 4;

    return (
        <div className="code-block">
            {/* Header : langage + bouton copier */}
            <div className="code-block-header">
        <span className="code-block-lang">
          <Terminal size={12} strokeWidth={2} />
            {lang}
        </span>
                <button
                    className={`code-block-copy ${copied ? "copied" : ""}`}
                    onClick={handleCopy}
                    aria-label="Copier le code"
                >
                    {copied ? <Check size={13} /> : <Copy size={13} />}
                    <span>{copied ? "Copié !" : "Copier"}</span>
                </button>
            </div>

            {/* Corps : code avec numéros de ligne optionnels */}
            <div className="code-block-body">
        <pre className="code-pre">
          {showLineNumbers && (
              <div className="line-numbers" aria-hidden="true">
                  {lines.map((_, i) => (
                      <span key={i}>{i + 1}</span>
                  ))}
              </div>
          )}
            <code className={`language-${lang}`}>{content}</code>
        </pre>
            </div>
        </div>
    );
}

// ─── Composant principal ──────────────────────────────────────────────────────
export default function MessageBubble({ message }: MessageBubbleProps) {
    const isUser = message.role === "user";
    const segments = parseSegments(message.content);

    return (
        <div className={`chat-msg ${isUser ? "user" : "assistant"}`}>
            <div className="msg-role">
                {isUser ? "VOUS" : "SERAPHIM"}
            </div>
            <div className="msg-content">
                {segments.map((seg, i) =>
                    seg.type === "code" ? (
                        <CodeBlock key={i} lang={seg.lang} content={seg.content} />
                    ) : (
                        <p key={i} className="message-text">
                            {seg.content}
                        </p>
                    )
                )}
            </div>
        </div>
    );
}
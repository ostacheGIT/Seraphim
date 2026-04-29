import { Message } from "../types";

interface MessageBubbleProps {
  message: Message;
}

function formatTime(date: Date): string {
  return new Date(date).toLocaleTimeString("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";

  return (
      <div className={`message-row ${isUser ? "user" : "assistant"}`}>
        {!isUser && (
            <div className="avatar assistant-avatar" aria-hidden="true">S</div>
        )}

        <div className="bubble-wrapper">
          <div className={`bubble ${isUser ? "user-bubble" : "assistant-bubble"}`}>
            {message.content}
          </div>
          <div className={`bubble-time ${isUser ? "right" : "left"}`}>
            {formatTime(message.timestamp)}
            {message.status === "pending" && (
                <span className="bubble-pending"> · en cours...</span>
            )}
          </div>
        </div>

        {isUser && (
            <div className="avatar user-avatar" aria-hidden="true">U</div>
        )}
      </div>
  );
}
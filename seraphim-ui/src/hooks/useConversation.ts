import { useState, useCallback, useEffect } from "react";
import { Conversation, Message } from "../types";

const API = "http://localhost:7272";

export type EngineId = string;

function generateId(): string {
  return Math.random().toString(36).slice(2, 9);
}

export function useConversation() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [engineId, setEngineId] = useState<EngineId>("auto");

  useEffect(() => {
    async function fetchSessions() {
      try {
        const res = await fetch(`${API}/memory/sessions`);
        if (!res.ok) return;
        const raw = (await res.json()) as {
          session_id: string;
          title: string;
          agent: string;
          updated_at: string;
        }[];
        const convos: Conversation[] = raw.map((s) => ({
          id: s.session_id,
          title: s.title
              ? s.title.slice(0, 42) + (s.title.length > 42 ? "..." : "")
              : s.session_id,
          messages: [],
          createdAt: new Date(s.updated_at),
          updatedAt: new Date(s.updated_at),
        }));
        setConversations(convos);
        if (convos.length > 0) setActiveId(convos[0].id);
      } catch (e) {
        console.error("Impossible de charger les sessions:", e);
      }
    }
    void fetchSessions();
  }, []);

  const selectConversation = useCallback(async (id: string) => {
    setActiveId(id);
    try {
      const res = await fetch(`${API}/memory/sessions/${id}`);
      if (!res.ok) return;
      const raw2 = (await res.json()) as {
        session: string;
        messages: { role: string; content: string }[];
      };
      const messages: Message[] = raw2.messages.map((m): Message => ({
        id: generateId(),
        role: m.role as "user" | "assistant",
        content: m.content,
        timestamp: new Date(),
        status: "done",
      }));
      setConversations((prev) =>
          prev.map((c) => (c.id === id ? { ...c, messages } : c)),
      );
    } catch (e) {
      console.error("Impossible de charger les messages:", e);
    }
  }, []);

  const active = conversations.find((c) => c.id === activeId) ?? null;

  const newConversation = useCallback(() => {
    const id = generateId();
    const convo: Conversation = {
      id,
      title: "Nouvelle conversation",
      messages: [
        {
          id: generateId(),
          role: "assistant",
          content: "Bonjour ! Je suis Seraphim. Parlez-moi ou écrivez ci-dessous.",
          timestamp: new Date(),
          status: "done",
        },
      ],
      createdAt: new Date(),
      updatedAt: new Date(),
    };
    setConversations((prev) => [convo, ...prev]);
    setActiveId(id);
  }, []);

  const deleteConversation = useCallback(
      async (id: string) => {
        try {
          await fetch(`${API}/memory/sessions/${id}`, { method: "DELETE" });
        } catch (e) {
          console.error("Erreur suppression session:", e);
        }
        setConversations((prev) => {
          const remaining = prev.filter((c) => c.id !== id);
          if (activeId === id && remaining.length > 0)
            setActiveId(remaining[0].id);
          return remaining;
        });
      },
      [activeId],
  );

  const replaceFromMessage = useCallback(
      (upToMessageId: string, newContent: string): string => {
        const newId = generateId();
        setConversations((prev) =>
            prev.map((c) => {
              if (c.id !== activeId) return c;
              const idx = c.messages.findIndex((m) => m.id === upToMessageId);
              if (idx === -1) return c;
              const newMsg: Message = {
                id: newId,
                role: "user",
                content: newContent,
                timestamp: new Date(),
                status: "done",
              };
              return { ...c, messages: [...c.messages.slice(0, idx), newMsg] };
            }),
        );
        return newId;
      },
      [activeId],
  );

  const truncateMessages = useCallback(
      async (sessionId: string, keepCount: number) => {
        try {
          await fetch(`${API}/memory/sessions/${sessionId}/truncate`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ keep_count: keepCount }),
          });
        } catch (e) {
          console.error("Truncate session failed:", e);
        }
      },
      [],
  );

  const addMessage = useCallback(
      (content: string, role: "user" | "assistant", status?: Message["status"], traceId?: string, imageUrl?: string) => {
        const msg: Message = {
          id: generateId(),
          role,
          content,
          timestamp: new Date(),
          status,
          traceId,
          imageUrl,
        };
        setConversations((prev) =>
            prev.map((c) => {
              if (c.id !== activeId) return c;
              const title =
                  c.title === "Nouvelle conversation" && role === "user"
                      ? content.slice(0, 42) + (content.length > 42 ? "..." : "")
                      : c.title;
              return {
                ...c,
                title,
                messages: [...c.messages, msg],
                updatedAt: new Date(),
              };
            }),
        );
        return msg.id;
      },
      [activeId],
  );

  const updateMessage = useCallback(
      (id: string, content: string, status?: Message["status"], traceId?: string) => {
        setConversations((prev) =>
            prev.map((c) => {
              if (c.id !== activeId) return c;
              return {
                ...c,
                messages: c.messages.map((m) =>
                    m.id === id
                        ? { ...m, content, ...(status !== undefined ? { status } : {}), ...(traceId !== undefined ? { traceId } : {}) }
                        : m,
                ),
                updatedAt: new Date(),
              };
            }),
        );
      },
      [activeId],
  );

  const updateConversationTitle = useCallback((id: string, title: string) => {
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, title } : c))
    );
  }, []);

  return {
    conversations,
    activeId,
    active,
    engineId,
    setEngineId,
    setActiveId: selectConversation,
    newConversation,
    deleteConversation,
    addMessage,
    updateMessage,
    replaceFromMessage,
    truncateMessages,
    updateConversationTitle,
  };
}
import { useState, useCallback, useEffect } from "react";
import { Conversation, Message } from "../types";

const API = "http://localhost:8000";

function generateId(): string {
  return Math.random().toString(36).slice(2, 9);
}

export function useConversation() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);

  useEffect(() => {
    async function fetchSessions() {
      try {
        const res = await fetch(`${API}/memory/sessions`);
        const raw = (await res.json()) as { session: string; preview: string; timestamp: string }[];
        const convos: Conversation[] = raw.map((s) => ({
          id: s.session,
          title: s.preview.slice(0, 42) + (s.preview.length > 42 ? "..." : ""),
          messages: [],
          createdAt: new Date(s.timestamp),
          updatedAt: new Date(s.timestamp),
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
          prev.map((c) => (c.id === id ? { ...c, messages } : c))
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
          if (activeId === id && remaining.length > 0) setActiveId(remaining[0].id);
          return remaining;
        });
      },
      [activeId]
  );

  const addMessage = useCallback(
      (content: string, role: "user" | "assistant", status?: Message["status"]) => {
        const msg: Message = {
          id: generateId(),
          role,
          content,
          timestamp: new Date(),
          status,
        };
        setConversations((prev) =>
            prev.map((c) => {
              if (c.id !== activeId) return c;
              const title =
                  c.title === "Nouvelle conversation" && role === "user"
                      ? content.slice(0, 42) + (content.length > 42 ? "..." : "")
                      : c.title;
              return { ...c, title, messages: [...c.messages, msg], updatedAt: new Date() };
            })
        );
        return msg.id;
      },
      [activeId]
  );

  return {
    conversations,
    activeId,
    active,
    setActiveId: selectConversation,
    newConversation,
    deleteConversation,
    addMessage,
  };
}
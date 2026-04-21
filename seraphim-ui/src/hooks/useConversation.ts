import { useState, useCallback } from "react";
import { Conversation, Message } from "../types";

function generateId(): string {
  return Math.random().toString(36).slice(2, 9);
}

const SEED: Conversation[] = [
  {
    id: "1",
    title: "Ouvre Chrome et va sur YouTube",
    messages: [
      {
        id: "m1",
        role: "assistant",
        content:
          "Bonjour ! Je suis Seraphim. Parlez-moi ou écrivez ci-dessous — je peux ouvrir des apps, gérer vos fichiers, envoyer des mails et bien plus encore.",
        timestamp: new Date(Date.now() - 120000),
        status: "done",
      },
      {
        id: "m2",
        role: "user",
        content: "Ouvre Chrome et va sur YouTube",
        timestamp: new Date(Date.now() - 60000),
        status: "done",
      },
      {
        id: "m3",
        role: "assistant",
        content: "Compris. J'ouvre Chrome et navigue vers YouTube...",
        timestamp: new Date(Date.now() - 55000),
        status: "done",
      },
    ],
    createdAt: new Date(Date.now() - 200000),
    updatedAt: new Date(Date.now() - 55000),
  },
  {
    id: "2",
    title: "Envoie un mail à Marc",
    messages: [
      {
        id: "m4",
        role: "assistant",
        content: "Bonjour ! Je suis Seraphim. Comment puis-je vous aider ?",
        timestamp: new Date(Date.now() - 300000),
        status: "done",
      },
    ],
    createdAt: new Date(Date.now() - 400000),
    updatedAt: new Date(Date.now() - 300000),
  },
  {
    id: "3",
    title: "Crée un dossier Projets",
    messages: [
      {
        id: "m5",
        role: "assistant",
        content: "Prêt à vous aider.",
        timestamp: new Date(Date.now() - 500000),
        status: "done",
      },
    ],
    createdAt: new Date(Date.now() - 600000),
    updatedAt: new Date(Date.now() - 500000),
  },
  {
    id: "4",
    title: "Refactor auth module",
    messages: [],
    createdAt: new Date(Date.now() - 86400000),
    updatedAt: new Date(Date.now() - 86400000),
  },
  {
    id: "5",
    title: "Résumé mes notes",
    messages: [],
    createdAt: new Date(Date.now() - 90000000),
    updatedAt: new Date(Date.now() - 90000000),
  },
];

export function useConversation() {
  const [conversations, setConversations] = useState<Conversation[]>(SEED);
  const [activeId, setActiveId] = useState<string>("1");

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
          content:
            "Bonjour ! Je suis Seraphim. Parlez-moi ou écrivez ci-dessous.",
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
    (id: string) => {
      setConversations((prev) => {
        const remaining = prev.filter((c) => c.id !== id);
        if (activeId === id && remaining.length > 0) {
          setActiveId(remaining[0].id);
        }
        return remaining;
      });
    },
    [activeId]
  );

  const addMessage = useCallback(
    (
      content: string,
      role: "user" | "assistant",
      status?: Message["status"]
    ) => {
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
              ? content.slice(0, 42)
              : c.title;
          return {
            ...c,
            title,
            messages: [...c.messages, msg],
            updatedAt: new Date(),
          };
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
    setActiveId,
    newConversation,
    deleteConversation,
    addMessage,
  };
}
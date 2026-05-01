const BASE = "http://localhost:7272";

const SENTENCE_END = /[.!?;:\n]/;

export type EngineId = "ollama_qwen3b" | "ollama_qwen7b";

export interface InstalledSkill {
  id: string;
  name: string;
  source: string;
  description: string;
}

export async function fetchInstalledSkills(): Promise<InstalledSkill[]> {
  const res = await fetch(`${BASE}/skills`);

  if (!res.ok) return [];
  const data = await res.json();
  return data.skills ?? [];
}

export async function askSeraphim(
    message: string,
    sessionId?: string,
    onToken?: (token: string) => void,
    onSentence?: (sentence: string) => void,
    engineId: EngineId = "ollama_qwen3b",
    agent: string = "react",
): Promise<string> {
  const res = await fetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query: message,
      agent: agent,
      model: engineId,
      engine_id: engineId,
      session_id: sessionId ?? null,
      messages: [],
      stream: true,
    }),
  });

  if (!res.ok) throw new Error(`Backend error: ${res.status}`);

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let full = "";
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      if (buffer.trim().length > 3) onSentence?.(buffer.trim());
      break;
    }
    const token = decoder.decode(value);
    full += token;
    buffer += token;
    onToken?.(token);

    if (SENTENCE_END.test(token)) {
      const sentence = buffer.trim();
      if (sentence.length > 3) onSentence?.(sentence);
      buffer = "";
    }
  }

  return full;
}
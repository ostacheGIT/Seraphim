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

export async function fetchNativeSkills(): Promise<InstalledSkill[]> {
  const res = await fetch(`${BASE}/skills/native`);
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
): Promise<{ response: string; traceId: string | null }> {
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

  const traceId = res.headers.get("X-Trace-Id");
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

  return { response: full, traceId };
}

export async function sendFeedback(traceId: string, score: number): Promise<void> {
  await fetch(`${BASE}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ trace_id: traceId, score }),
  });
}

export interface CatalogSkill {
  name: string;
  slug: string;
  description: string;
  source: string;
  category: string;
}

export async function searchSkillCatalog(
  q: string = "",
  limit: number = 200,
  offset: number = 0,
  source: string = "",
): Promise<{ skills: CatalogSkill[]; catalog_size: number }> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (q) params.set("q", q);
  if (source) params.set("source", source);
  const res = await fetch(`${BASE}/skills/catalog?${params}`);
  if (!res.ok) return { skills: [], catalog_size: 0 };
  return res.json();
}

export async function installSkill(
  name: string,
  source: string,
): Promise<{ success: boolean; skipped: boolean; skill_name: string; warnings: string[] }> {
  const res = await fetch(`${BASE}/skills/install`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, source }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Install failed");
  }
  return res.json();
}

export async function buildSkillCatalog(): Promise<number> {
  const res = await fetch(`${BASE}/skills/catalog/build`, { method: "POST" });
  if (!res.ok) throw new Error("Build failed");
  const data = await res.json();
  return data.indexed ?? 0;
}
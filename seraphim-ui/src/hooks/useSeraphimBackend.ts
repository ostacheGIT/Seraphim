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
    image?: string,
    contextMessages?: { role: string; content: string }[],
    signal?: AbortSignal,
): Promise<{ response: string; traceId: string | null }> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: message,
        agent: agent,
        model: engineId,
        engine_id: engineId,
        session_id: sessionId ?? null,
        messages: contextMessages ?? [],
        stream: true,
        image: image ?? null,
      }),
      signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") return { response: "", traceId: null };
    throw e;
  }

  if (!res.ok) throw new Error(`Backend error: ${res.status}`);

  const traceId = res.headers.get("X-Trace-Id");
  if (!res.body) throw new Error("Response body is null — streaming not supported");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let full = "";
  let buffer = "";

  while (true) {
    let done: boolean, value: Uint8Array | undefined;
    try {
      ({ done, value } = await reader.read());
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") break;
      throw e;
    }
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

export async function warmupEngine(engineId: string): Promise<void> {
  try {
    await fetch(`${BASE}/engine/warmup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ engine_id: engineId }),
    });
  } catch { /* silent — warmup is best-effort */ }
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

export async function generateSessionTitle(sessionId: string, text?: string): Promise<string | null> {
  try {
    const res = await fetch(`${BASE}/memory/sessions/${sessionId}/title`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text ?? null }),
    });
    if (!res.ok) return null;
    const data = await res.json() as { title: string };
    return data.title || null;
  } catch { return null; }
}

// ── User Facts ────────────────────────────────────────────────────────────────

export async function getUserFacts(): Promise<Record<string, string>> {
  try {
    const res = await fetch(`${BASE}/memory/facts`);
    if (!res.ok) return {};
    const data = await res.json() as { facts: Record<string, string> };
    return data.facts ?? {};
  } catch { return {}; }
}

export async function setUserFact(key: string, value: string): Promise<boolean> {
  try {
    const res = await fetch(`${BASE}/memory/facts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, value }),
    });
    return res.ok;
  } catch { return false; }
}

export async function deleteUserFact(key: string): Promise<boolean> {
  try {
    const res = await fetch(`${BASE}/memory/facts/${encodeURIComponent(key)}`, { method: "DELETE" });
    return res.ok;
  } catch { return false; }
}

// ── RAG ───────────────────────────────────────────────────────────────────────

export async function getRagStatus(): Promise<{ enabled: boolean; doc_count: number }> {
  try {
    const res = await fetch(`${BASE}/rag/status`);
    if (!res.ok) return { enabled: false, doc_count: 0 };
    return res.json();
  } catch { return { enabled: false, doc_count: 0 }; }
}

export async function ingestToRAG(content: string, source: string = "manual"): Promise<number> {
  try {
    const res = await fetch(`${BASE}/rag/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, source }),
    });
    if (!res.ok) return 0;
    const data = await res.json() as { ingested_chunks: number };
    return data.ingested_chunks ?? 0;
  } catch { return 0; }
}

export async function resetRAG(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE}/rag/reset`, { method: "DELETE" });
    return res.ok;
  } catch { return false; }
}

// ── Session search ────────────────────────────────────────────────────────────

export interface SessionSummary {
  session: string;
  agent: string;
  preview: string;
  timestamp: string;
}

export async function searchSessions(query: string): Promise<SessionSummary[]> {
  try {
    const res = await fetch(`${BASE}/memory/search?q=${encodeURIComponent(query)}`);
    if (!res.ok) return [];
    const data = await res.json() as { sessions: SessionSummary[] };
    return data.sessions ?? [];
  } catch { return []; }
}
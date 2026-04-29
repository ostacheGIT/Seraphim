const BASE = "http://localhost:7272";

// Ponctuation qui marque une fin de phrase jouable

const SENTENCE_END = /[.!?;:\n]/;

export async function askSeraphim(
    message: string,
    sessionId?: string,
    onToken?: (token: string) => void,
    onSentence?: (sentence: string) => void
): Promise<string> {
  const res = await fetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query:      message,
      agent:      "chat",
      model:      "qwen2.5:3b",
      session_id: sessionId ?? null,
      messages:   [],
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

// ----------------------------------------------------------------
// MODE 3 — Tauri invoke (Rust natif)
// ----------------------------------------------------------------
// import { invoke } from "@tauri-apps/api/core";
// export async function askSeraphim(message: string): Promise<string> {
//   return invoke<string>("ask_seraphim", { message });
// }
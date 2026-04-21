// ================================================================
// Connexion au backend Seraphim
// Choisis un mode et supprime les autres
// ================================================================

// MODE 2 & 3 — décommente si besoin
// import { invoke } from "@tauri-apps/api/core";

// ----------------------------------------------------------------
// MODE 1 — Simulation (dev, actif par défaut)
// ----------------------------------------------------------------
//export async function askSeraphim(message: string): Promise<string> {
//  await new Promise<void>((r) => setTimeout(r, 800 + Math.random() * 700));
//  return `Commande reçue : "${message}". Je traite ça maintenant...`;
//}


// ----------------------------------------------------------------
// MODE 2 — FastAPI Python (localhost:7272)
// Remplace la fonction ci-dessus par celle-ci quand ton backend est prêt
// ----------------------------------------------------------------
const BASE = "http://localhost:7272";

export async function askSeraphim(
    message: string,
    onToken?: (token: string) => void
): Promise<string> {
  const res = await fetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query: message,
      agent: "chat",
      model: "llama3.2:1b",  // ← plus rapide sur 4GB VRAM
      messages: [],
    }),
  });

  if (!res.ok) throw new Error(`Backend error: ${res.status}`);

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let full = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const token = decoder.decode(value);
    full += token;
    onToken?.(token);
  }

  return full;
}


// ----------------------------------------------------------------
// MODE 3 — Tauri invoke (Rust natif)
// ----------------------------------------------------------------
// export async function askSeraphim(message: string): Promise<string> {
//   return invoke<string>("ask_seraphim", { message });
// }
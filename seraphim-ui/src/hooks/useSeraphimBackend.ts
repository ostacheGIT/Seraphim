// ================================================================
// Connexion au backend Seraphim
// Choisis un mode et supprime les autres
// ================================================================

// MODE 2 & 3 — décommente si besoin
// import { invoke } from "@tauri-apps/api/core";

// ----------------------------------------------------------------
// MODE 1 — Simulation (dev, actif par défaut)
// ----------------------------------------------------------------
export async function askSeraphim(message: string): Promise<string> {
  await new Promise<void>((r) => setTimeout(r, 800 + Math.random() * 700));
  return `Commande reçue : "${message}". Je traite ça maintenant...`;
}


// ----------------------------------------------------------------
// MODE 2 — FastAPI Python (localhost:8000)
// Remplace la fonction ci-dessus par celle-ci quand ton backend est prêt
// ----------------------------------------------------------------
// export async function askSeraphim(message: string): Promise<string> {
//   const res = await fetch("http://localhost:8000/ask", {
//     method: "POST",
//     headers: { "Content-Type": "application/json" },
//     body: JSON.stringify({ message }),
//   });
//   if (!res.ok) throw new Error(`Backend error: ${res.status}`);
//   const data = await res.json();
//   return data.response as string;
// }


// ----------------------------------------------------------------
// MODE 3 — Tauri invoke (Rust natif)
// ----------------------------------------------------------------
// export async function askSeraphim(message: string): Promise<string> {
//   return invoke<string>("ask_seraphim", { message });
// }
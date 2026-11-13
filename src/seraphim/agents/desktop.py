"""DesktopAgent — un agent qui regarde l'écran et agit à la souris et au clavier.

Boucle observer → décider → agir :
  1. observe()  : fenêtre cible + éléments numérotés (UI Automation, OCR en secours)
  2. LLM        : choisit UNE action en JSON (clic, saisie, raccourci, défilement…)
  3. exécution  : pywinauto (SendInput), puis on ré-observe pour vérifier l'effet

Sécurité :
  - arrêt d'urgence : placer la souris dans le coin supérieur gauche de l'écran
  - nombre d'étapes limité (settings.desktop.max_steps)
  - arrêt avant un clic/une saisie « supprimer / payer / envoyer… » non demandé
  - détection des boucles (même action répétée)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass
from typing import AsyncIterator

from seraphim.agents.core import AgentContext, BaseAgent

logger = logging.getLogger(__name__)

ACTIONS = [
    "click", "double_click", "right_click", "type", "key", "scroll",
    "open_app", "focus_window", "wait", "read_screen", "describe_screen",
    "done", "fail",
]

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "action": {"type": "string", "enum": ACTIONS},
        "target": {"type": "integer"},
        "text": {"type": "string"},
    },
    "required": ["thought", "action"],
}

SYSTEM_PROMPT = """Tu es Seraphim et tu utilises un PC Windows à la souris et au clavier.
Tu reçois la tâche, tes actions précédentes et l'écran actuel : fenêtre active, autres fenêtres, éléments « [id] Type nom ».
Réponds UNIQUEMENT en JSON : {"thought": "...", "action": "...", "target": id, "text": "..."} — thought en 15 mots maximum.

Actions :
click / double_click / right_click (target) — clic sur l'élément [id]
type (text, target facultatif) — écrit le texte, après un clic sur target si donné
key (text) — raccourci : "enter", "ctrl+l", "alt+tab", "ctrl+a, delete"
scroll (text = "up" ou "down", target facultatif)
open_app (text) — lance une application, ex "chrome"
focus_window (text) — active la fenêtre dont le titre contient text
wait — attendre un chargement
read_screen — lire tout le texte de l'écran
describe_screen (text = question) — analyse visuelle (lent)
done (text) — tâche finie ; text = réponse en français pour l'utilisateur
fail (text) — impossible ; text = pourquoi

Règles : une action par réponse ; uniquement des [id] de la liste actuelle ; vérifie l'effet de l'action précédente ;
site web = focus du navigateur puis key "ctrl+l", type l'adresse, key "enter" ;
question sur l'écran = réponds tout de suite avec done ; ne répète pas une action sans effet ;
jamais supprimer/payer/envoyer sans demande explicite ; done dès que c'est fini."""

# Familles d'actions sensibles : un mot de la famille dans la cible bloque
# l'action, sauf si la demande de l'utilisateur contient un mot de la même famille.
_RISKY_FAMILIES: dict[str, re.Pattern] = {
    "suppression": re.compile(
        r"supprim|delete|effac|corbeille|recycle|d[ée]sinstall|uninstall|formater|format\b|remove|retirer|vider", re.I),
    "paiement": re.compile(
        r"pay|achet|buy|command|order|purchase|checkout|abonn|subscri|r[ée]gler", re.I),
    "envoi": re.compile(
        r"envoy|envoi|send|publi|post|partag|share|transf[ée]r|r[ée]pondre|reply", re.I),
    "système": re.compile(
        r"[ée]teindre|arr[êe]ter le syst|shut ?down|red[ée]marr|restart|d[ée]connex|sign out|log ?off", re.I),
}


def risky_family(label: str, query: str) -> str | None:
    """Retourne la famille sensible touchée par `label` si la demande ne l'autorise pas."""
    for family, rx in _RISKY_FAMILIES.items():
        if rx.search(label) and not rx.search(query):
            return family
    return None


def parse_decision(raw: str) -> dict:
    """Extrait le JSON de décision de la réponse du LLM (tolérant)."""
    raw = (raw or "").strip()
    candidates = [raw]
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        candidates.append(m.group(0))
    for c in candidates:
        try:
            d = json.loads(c)
            if isinstance(d, dict) and d.get("action"):
                d["action"] = str(d["action"]).strip().lower()
                t = d.get("target")
                if isinstance(t, str):
                    t = re.sub(r"\D", "", t)
                    d["target"] = int(t) if t else None
                return d
        except (json.JSONDecodeError, ValueError):
            continue
    raise ValueError(f"Réponse non exploitable : {raw[:200]}")


@dataclass
class StepResult:
    summary: str          # ce qui a été fait (affiché à l'utilisateur)
    feedback: str         # retour pour le LLM au tour suivant
    finished: bool = False
    answer: str = ""


class DesktopAgent(BaseAgent):
    name = "desktop"
    description = (
        "Agent bureau — regarde l'écran (UI Automation, OCR, vision) et agit à la souris "
        "et au clavier pour accomplir une tâche sur le PC"
    )
    system_prompt = SYSTEM_PROMPT
    _auto_trace = False

    # ── LLM ──────────────────────────────────────────────────────────────────

    @staticmethod
    async def _ollama_decide(eng, messages: list[dict]) -> str:
        """Appel Ollama dédié : sortie JSON contrainte, délai long (CPU lent),
        même num_ctx que les autres agents pour éviter un rechargement du modèle."""
        import httpx
        from seraphim.settings import settings

        cfg = settings.desktop
        payload = {
            "model": getattr(eng, "model", settings.engine.model),
            "messages": messages,
            "stream": False,
            "format": DECISION_SCHEMA,
            "keep_alive": "30m",
            "options": {
                "temperature": 0.1,
                "num_ctx": settings.engine.context_window,
                "num_predict": 160,
            },
        }
        base = getattr(eng, "base_url", settings.engine.base_url).rstrip("/")
        async with httpx.AsyncClient(base_url=base, timeout=httpx.Timeout(cfg.llm_timeout, connect=10)) as c:
            r = await c.post("/api/chat", json=payload)
            r.raise_for_status()
            return r.json().get("message", {}).get("content", "") or ""

    async def _decide(self, task: str, history: list[str], obs_text: str) -> dict:
        hist = "\n".join(history[-8:]) or "(aucune action pour l'instant)"
        user = (
            f"Tâche : {task}\n\n"
            f"Historique :\n{hist}\n\n"
            f"État actuel de l'écran :\n{obs_text}\n\n"
            "Quelle est la prochaine action ? Réponds en JSON."
        )
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
        eng = self.engine
        last_err: Exception | None = None
        for attempt in range(2):
            if getattr(eng, "id", "") == "ollama":
                raw = await self._ollama_decide(eng, messages)
            else:
                raw = await self._chat(messages)
            try:
                return parse_decision(raw)
            except ValueError as exc:
                last_err = exc
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": "Réponds uniquement avec l'objet JSON demandé."})
        raise last_err or ValueError("pas de décision")

    # ── Exécution d'une action ───────────────────────────────────────────────

    @staticmethod
    def _bring_to_front(hwnd: int) -> None:
        """Remet la fenêtre de travail devant si l'utilisateur a cliqué ailleurs."""
        import win32gui
        from seraphim.desktop import computer as pc

        if win32gui.GetForegroundWindow() != hwnd:
            pc.focus_window(hwnd)

    async def _execute(self, d: dict, obs, task: str, cfg) -> StepResult:
        from seraphim.desktop import computer as pc

        action = d.get("action", "")
        text = str(d.get("text") or "").strip()
        el = obs.element(d.get("target"))

        def need_el() -> StepResult | None:
            if el is None:
                return StepResult(
                    summary=f"⚠️ {action} : élément [{d.get('target')}] introuvable",
                    feedback=f"ERREUR : l'élément [{d.get('target')}] n'existe pas dans la liste actuelle.",
                )
            return None

        if cfg.confirm_risky and action in ("click", "double_click", "type", "key"):
            label = " ".join(filter(None, [el.name if el else "", text if action == "key" else ""]))
            fam = risky_family(label, task)
            if fam:
                msg = (f"Je me suis arrêté avant d'agir sur « {label} » (action sensible : {fam}). "
                       "Si c'est bien ce que tu veux, redemande-le explicitement.")
                return StepResult(summary=f"⛔ {msg}", feedback="", finished=True, answer=msg)

        if action in ("click", "double_click", "right_click", "type", "key", "scroll") and obs.window_hwnd:
            await asyncio.to_thread(self._bring_to_front, obs.window_hwnd)

        if action in ("click", "double_click", "right_click"):
            if (err := need_el()):
                return err
            button = "right" if action == "right_click" else "left"
            await asyncio.to_thread(pc.click, el.x, el.y, button, action == "double_click")
            verb = {"click": "Clic", "double_click": "Double-clic", "right_click": "Clic droit"}[action]
            return StepResult(f"{verb} sur « {el.name} »", f"{verb} effectué sur [{el.id}] « {el.name} ».")

        if action == "type":
            if not text:
                return StepResult("⚠️ type sans texte", "ERREUR : 'text' est vide.")
            if el is not None:
                await asyncio.to_thread(pc.click, el.x, el.y)
                await asyncio.sleep(0.2)
            await asyncio.to_thread(pc.type_text, text)
            where = f" dans « {el.name} »" if el else ""
            short = text if len(text) <= 60 else text[:57] + "…"
            return StepResult(f"Saisie « {short} »{where}", f"Texte saisi{where}.")

        if action == "key":
            if not text:
                return StepResult("⚠️ key sans touches", "ERREUR : 'text' doit contenir les touches.")
            try:
                pc.keys_to_pywinauto(text)
            except ValueError as exc:
                return StepResult(f"⚠️ raccourci invalide « {text} »", f"ERREUR : {exc}")
            await asyncio.to_thread(pc.press_keys, text)
            return StepResult(f"Touches « {text} »", f"Touches « {text} » envoyées.")

        if action == "scroll":
            direction = "up" if text.lower() in ("up", "haut") else "down"
            x, y = (el.x, el.y) if el else pc.window_center(obs.window_hwnd)
            await asyncio.to_thread(pc.scroll, x, y, direction)
            return StepResult(f"Défilement vers le {'haut' if direction == 'up' else 'bas'}",
                              f"Défilement {direction} effectué.")

        if action == "open_app":
            from seraphim.skills.registry import SKILL_REGISTRY, discover_skills
            if not SKILL_REGISTRY:
                discover_skills()
            skill = SKILL_REGISTRY.get("app_manager")
            if not skill or not text:
                return StepResult("⚠️ open_app impossible", "ERREUR : application non précisée.")
            r = await skill.run(action="open", app=text)
            await asyncio.sleep(2.0)
            self._target_hwnd = None  # se caler sur la fenêtre qui vient de s'ouvrir
            return StepResult(f"Ouverture de {text}", r.output if r.success else f"ERREUR : {r.error}")

        if action == "focus_window":
            found = await asyncio.to_thread(pc.find_window, text)
            if not found:
                return StepResult(f"⚠️ fenêtre « {text} » introuvable",
                                  f"ERREUR : aucune fenêtre ne contient « {text} ». Utilise un titre de la liste.")
            hwnd, title = found
            ok = await asyncio.to_thread(pc.focus_window, hwnd)
            self._target_hwnd = hwnd
            return StepResult(f"Fenêtre « {title} » au premier plan",
                              f"Fenêtre « {title} » activée." if ok else "ERREUR : impossible d'activer la fenêtre.")

        if action == "wait":
            await asyncio.sleep(2.0)
            return StepResult("Attente…", "Attente de 2 s effectuée.")

        if action == "read_screen":
            txt = await asyncio.to_thread(pc.screen_text)
            return StepResult("Lecture du texte à l'écran (OCR)",
                              "Texte lu à l'écran :\n" + (txt[:2500] or "(aucun texte)"))

        if action == "describe_screen":
            from seraphim.settings import settings
            model = cfg.vision_model
            if not await pc.vision_model_available(model, settings.engine.base_url):
                txt = await asyncio.to_thread(pc.screen_text)
                return StepResult(
                    "Analyse de l'écran (OCR — pas de modèle de vision installé)",
                    "Aucun modèle de vision configuré ; voici le texte lu à l'écran :\n" + txt[:2500],
                )
            desc = await pc.describe_screen(text or task, model, settings.engine.base_url)
            return StepResult(f"Analyse visuelle de l'écran ({model})", "Description de l'écran :\n" + desc[:2500])

        if action in ("done", "fail"):
            answer = text or ("Tâche terminée." if action == "done" else "Je n'ai pas réussi.")
            return StepResult(summary="", feedback="", finished=True,
                              answer=answer if action == "done" else f"❌ {answer}")

        return StepResult(f"⚠️ action inconnue « {action} »", f"ERREUR : action inconnue « {action} ».")

    # ── Boucle principale ────────────────────────────────────────────────────

    async def steps(self, task: str) -> AsyncIterator[str]:
        if sys.platform != "win32":
            yield "L'agent bureau ne fonctionne que sous Windows."
            return
        try:
            from seraphim.desktop import computer as pc
        except ImportError as exc:
            yield f"Dépendance manquante pour l'agent bureau : {exc}. Lance `uv sync`."
            return
        from seraphim.settings import settings

        cfg = settings.desktop
        self._target_hwnd: int | None = None
        history: list[str] = []
        last_sig: list[str] = []

        yield "🖥️ Je prends la main sur l'écran (souris dans le coin haut-gauche = arrêt d'urgence).\n"

        for step in range(1, cfg.max_steps + 1):
            if await asyncio.to_thread(pc.emergency_stop_requested):
                yield "\n⛔ Arrêt d'urgence demandé (souris dans le coin haut-gauche)."
                return

            obs = await asyncio.to_thread(pc.observe, self._target_hwnd)
            self._target_hwnd = obs.window_hwnd or self._target_hwnd

            try:
                d = await self._decide(task, history, obs.to_prompt())
            except Exception as exc:
                logger.warning("desktop decide failed: %s", exc)
                yield f"\n❌ Le modèle n'a pas fourni d'action exploitable ({type(exc).__name__}: {exc})."
                return

            sig = f"{d.get('action')}|{d.get('target')}|{d.get('text')}"
            last_sig = (last_sig + [sig])[-3:]
            if len(last_sig) == 3 and len(set(last_sig)) == 1 and d.get("action") not in ("wait",):
                yield "\n❌ Je tourne en rond (même action répétée 3 fois) — j'arrête."
                return

            if await asyncio.to_thread(pc.emergency_stop_requested):
                yield "\n⛔ Arrêt d'urgence demandé (souris dans le coin haut-gauche)."
                return

            try:
                res = await self._execute(d, obs, task, cfg)
            except Exception as exc:
                logger.exception("desktop action failed")
                res = StepResult(f"⚠️ échec de {d.get('action')}", f"ERREUR pendant l'action : {exc}")

            if res.finished:
                if res.summary:
                    yield f"{step}. {res.summary}\n"
                yield f"\n{res.answer}"
                return

            yield f"{step}. {res.summary}\n"
            history.append(
                f"Étape {step} : {d.get('action')}"
                + (f" [{d.get('target')}]" if d.get("target") is not None else "")
                + (f" « {d.get('text')} »" if d.get("text") else "")
                + f" → {res.feedback}"
            )
            await asyncio.sleep(cfg.settle_delay)

        yield f"\n⚠️ Nombre maximal d'étapes atteint ({cfg.max_steps}) sans terminer la tâche."

    async def stream(self, query: str, context: AgentContext | None = None):
        async for chunk in self.steps(query):
            yield chunk

    async def run(self, query: str, context: AgentContext | None = None) -> str:
        return "".join([c async for c in self.steps(query)])

"""DesktopAgent — un agent qui regarde l'écran, répond sur ce qu'il voit et agit
à la souris et au clavier.

Deux modes, choisis par le CODE (pas par le modèle) :

  Mode regard — question ou demande d'aide (« c'est quoi cette erreur ? »,
  « aide-moi avec ce popup ») :
    capture de la fenêtre → modèle de vision → réponse en texte libre, streamée.
    Si une action aiderait, le modèle l'écrit sur une ligne « ACTION: … » :
    elle devient une PROPOSITION, exécutée seulement si l'utilisateur répond « oui ».
    Aucune action souris/clavier n'est possible dans ce mode.

  Mode action — ordre explicite (« clique sur… », « ouvre… ») ou proposition validée :
    boucle observer → décider → agir :
      1. observe()  : fenêtre cible + éléments numérotés (UI Automation)
      2. vision     : capture où les éléments sont encadrés et numérotés, envoyée au
                      modèle de vision qui choisit UNE action en JSON.
                      Sans modèle de vision : mode texte (UIA + OCR) avec le modèle principal.
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
from collections.abc import AsyncIterator
from dataclasses import dataclass

from seraphim.agents.core import AgentContext, BaseAgent

logger = logging.getLogger(__name__)

ACTIONS = [
    "click_on", "open_item",
    "click", "double_click", "right_click", "click_at", "type", "key", "scroll",
    "open_app", "focus_window", "wait", "read_screen", "describe_screen",
    "done", "fail",
]
_INPUT_ACTIONS = {"click_on", "open_item", "click", "double_click", "right_click", "click_at",
                  "type", "key", "scroll"}

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "action": {"type": "string", "enum": ACTIONS},
        "target": {"type": "integer"},
        "x": {"type": "integer"},
        "y": {"type": "integer"},
        "text": {"type": "string"},
    },
    "required": ["thought", "action"],
}

_ACTIONS_DOC = """click_on (text) — trouve n'importe où à l'écran (toute fenêtre, barre des tâches, bureau) ce qui affiche le texte text et clique dessus
open_item (text) — trouve et OUVRE ce qui affiche le texte text : fichier, dossier, lien, icône, application…
click / double_click / right_click (target) — clic sur l'élément [id]
{click_at}type (text, target facultatif) — écrit le texte, après un clic sur target si donné
key (text) — raccourci : "enter", "ctrl+l", "alt+tab", "ctrl+a, delete"
scroll (text = "up" ou "down", target facultatif)
open_app (text) — lance une application, ex "chrome"
focus_window (text) — active la fenêtre dont le titre contient text
wait — attendre un chargement
read_screen — lire tout le texte de l'écran (OCR){describe}
done (text) — tâche finie ; text = ce que tu as fait, en français
fail (text) — impossible ; text = pourquoi"""

_RULES = """Règles : une action par réponse ; uniquement des [id] de la liste actuelle ; vérifie l'effet de l'action précédente ;
ne répète pas une action sans effet ; site web = focus du navigateur puis key "ctrl+l", type l'adresse, key "enter" ;
jamais supprimer/payer/envoyer sans demande explicite ; done dès que c'est fini.
Pour cliquer ou ouvrir une chose visible, préfère click_on / open_item avec son texte exact tel qu'affiché."""

_CLICK_AT_DOC = ("click_at (x, y, text) — clic à la position (x, y) en pixels de la capture, "
                 "si la cible n'a pas de numéro ; text = ce que tu cliques\n")
_DESCRIBE_DOC = "\ndescribe_screen (text = question) — analyse visuelle (lent)"

SYSTEM_PROMPT_VISION = f"""Tu es Seraphim et tu utilises le PC Windows de l'utilisateur à la souris et au clavier pour exécuter sa demande.
Tu reçois la demande, tes actions précédentes, la liste des éléments « [id] Type nom » et une capture de la fenêtre où ces éléments sont encadrés en rose avec leur numéro.
Regarde la capture pour savoir où tu en es.
Réponds UNIQUEMENT en JSON : {{"thought": "...", "action": "...", ...}} — thought en 15 mots maximum.

Actions :
{_ACTIONS_DOC.format(click_at=_CLICK_AT_DOC, describe="")}

{_RULES} Préfère un [id] à click_at ; pour click_at, donne toujours x et y."""

SYSTEM_PROMPT = f"""Tu es Seraphim et tu utilises le PC Windows de l'utilisateur à la souris et au clavier pour exécuter sa demande.
Tu reçois la demande, tes actions précédentes et l'écran actuel : fenêtre active, autres fenêtres, éléments « [id] Type nom ».
Réponds UNIQUEMENT en JSON : {{"thought": "...", "action": "...", "target": id, "text": "..."}} — thought en 15 mots maximum.

Actions :
{_ACTIONS_DOC.format(click_at="", describe=_DESCRIBE_DOC)}

{_RULES}"""

LOOK_PROMPT = """Tu es Seraphim, l'assistant de l'utilisateur. Tu vois son écran : {source}.
Réponds à sa demande en français, de façon concise et factuelle, en t'appuyant sur ce que tu vois réellement.
Ne décris pas l'interface en général : réponds à ce qui est demandé. Si tu ne vois pas l'information, dis-le.
Si une action à l'écran (clic, saisie, raccourci, ouverture d'un fichier…) aiderait l'utilisateur, termine par une ligne seule :
ACTION: <un ordre court avec le texte EXACT affiché à l'écran, ex : clique sur « Réessayer » ou : ouvre « README.md »>
Sinon, n'écris pas de ligne ACTION."""

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

# Formules de politesse avant le verbe : « Seraphim, », « est-ce que tu peux »,
# « vous pouvez », « je voudrais que tu »… (dictée vocale comprise)
_POLITE_PREFIX = (
    r"(?:(?:seraphim|stp|s['’]il\s+te\s+pla[iî]t|s['’]il\s+vous\s+pla[iî]t|please|ok|alors|bon)[\s,!]+)?"
    r"(?:(?:est[- ]ce\s+que\s+)?(?:tu\s+peux|peux[- ]tu|pourrais[- ]tu|tu\s+pourrais|"
    r"vous\s+pouvez|pouvez[- ]vous|pourriez[- ]vous|vous\s+pourriez|"
    r"(?:je\s+(?:veux|voudrais)|j['’]aimerais)(?:\s+que\s+(?:tu|vous))?|"
    r"can\s+you|could\s+you)\s+)?"
    r"(?:m['’]|me\s+)?"
)

# Ordre explicite : la demande commence par un verbe d'action (éventuellement
# précédé de « Seraphim, », « peux-tu », « please »…). Tout le reste (questions,
# demandes d'aide, constats) passe en mode regard, sans souris ni clavier.
_ORDER_RE = re.compile(
    r"^\s*" + _POLITE_PREFIX +
    r"(?:(?:double[- ]?)?clique[rz]?|clic|appuie|presse|tape|[ée]cri[st]|saisis|remplis|coche|d[ée]coche|"
    r"s[ée]lectionne|choisis|ouvre|ferme|lance|d[ée]marre|va\s+(?:sur|dans|à|au|en|voir)|navigue|"
    r"fais\s+d[ée]filer|scrolle?|descends|remonte|copie|colle|coupe|enregistre|sauvegarde|"
    r"accepte|refuse|valide|annule|r[ée]duis|agrandis|maximise|minimise|d[ée]place|glisse|"
    r"mets|joue|relance|t[ée]l[ée]charge|installe|supprime|efface|envoie|r[ée]ponds|"
    r"active|d[ée]sactive|zoome|d[ée]zoome|reviens|retourne|rafra[iî]chis|actualise|recharge|"
    r"utilise|renomme|cr[ée]e|ajoute|retire|remplace|modifie|change|"
    r"click|double[- ]click|press|type|write|fill|check|uncheck|select|choose|open|close|launch|start|"
    r"go\s+to|scroll|copy|paste|save|accept|reject|cancel|play|pause|download|install|delete|"
    r"send|reply|enable|disable|refresh|reload|rename|create|add|remove|change|edit|"
    # infinitifs après « tu peux / peux-tu » qui ne sont pas « verbe + r »
    r"ouvrir|[ée]crire|remplir|saisir|choisir|r[ée]duire|agrandir|mettre|rafra[iî]chir|revenir|"
    r"descendre|faire\s+d[ée]filer|aller\s+(?:sur|dans|à|au|en|voir))(?:r|z|s)?\b",
    re.I,
)

# La réponse du mode regard dit à l'utilisateur de faire quelque chose
_SUGGESTS_ACTION_RE = re.compile(
    r"\b(?:ouvre[zs]?|clique[zs]?|appuie[zs]?|appuyez|tape[zs]?|s[ée]lectionne[zs]?|ferme[zs]?|"
    r"allez|va\s+sur|double[- ]clique[zs]?|coche[zs]?|saisi(?:s|ssez)|entre[zs]|lance[zs]?|"
    r"rel?ance[zs]?|valide[zs]?|accepte[zs]?|fai(?:s|tes)\s+d[ée]filer|click|open|press|select)\b",
    re.I,
)

_ACTION_LINE_RE = re.compile(r"^\s*\**\s*ACTION\s*\**\s*:\s*\**\s*(.+?)\s*\**\s*$", re.I)


def risky_family(label: str, query: str) -> str | None:
    """Retourne la famille sensible touchée par `label` si la demande ne l'autorise pas."""
    for family, rx in _RISKY_FAMILIES.items():
        if rx.search(label) and not rx.search(query):
            return family
    return None


_SIMPLE_ORDER_RE = re.compile(
    r"^\s*" + _POLITE_PREFIX +
    r"(?P<verb>double[- ]?clique[rzs]?|double[- ]?click|"
    r"(?:fais|faire|fasses)\s+un\s+clic\s+droit|clique[rzs]?\s+droit|right[- ]?click|"
    r"clique[rzs]?|click|appuie[rzs]?|s[ée]lectionne[rzs]?|coche[rzs]?|d[ée]coche[rzs]?|"
    r"ouvre[rzs]?|ouvrir|open|lance[rzs]?)"
    r"(?:[- ]moi)?"
    r"\s+(?:sur\s+|dans\s+|on\s+)?(?P<target>.+?)\s*[.!?]*\s*$",
    re.I,
)
_CHAINED_RE = re.compile(r"\b(?:et|puis|ensuite|apr[eè]s|then|and)\b|[,;]", re.I)


def parse_simple_order(query: str) -> tuple[str, str] | None:
    """« clique sur Paramètres » → ("click", "Paramètres") ; « ouvre README.md » →
    ("open", "README.md"). None si l'ordre enchaîne plusieurs étapes."""
    m = _SIMPLE_ORDER_RE.match(query or "")
    if not m:
        return None
    target = m.group("target").strip()
    from seraphim.desktop.activate import split_location

    core = split_location(target)[0]       # « … et tu vois dans Windows Explorer » ne compte pas
    if not core or _CHAINED_RE.search(core) or len(core) > 80:
        return None
    verb = m.group("verb").lower()
    if "droit" in verb or "right" in verb:
        mode = "right"
    elif verb.startswith("double"):
        mode = "double"
    elif verb.startswith(("ouv", "open", "lanc")):
        mode = "open"
    else:
        mode = "click"
    return mode, target


def is_order(query: str) -> bool:
    """Ordre explicite d'agir à l'écran (« clique sur… », « peux-tu ouvrir… »)."""
    return bool(_ORDER_RE.match(query or ""))


def _to_int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        v = re.sub(r"[^\d-]", "", v)
        return int(v) if v not in ("", "-") else None
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
                for k in ("target", "x", "y"):
                    if k in d:
                        d[k] = _to_int(d[k])
                return d
        except (json.JSONDecodeError, ValueError):
            continue
    raise ValueError(f"Réponse non exploitable : {raw[:200]}")


def split_action_line(text: str) -> tuple[str, str]:
    """Sépare la réponse du mode regard et sa ligne « ACTION: … » éventuelle."""
    kept, action = [], ""
    for line in (text or "").splitlines():
        m = _ACTION_LINE_RE.match(line)
        if m:
            action = m.group(1).strip()
            if len(action) > 1 and action[0] in "«\"'" and action[-1] in "»\"'":
                action = action[1:-1].strip()
        else:
            kept.append(line)
    if action.lower() in ("aucune", "none", "rien", "-", "n/a"):
        action = ""
    return "\n".join(kept).strip(), action


@dataclass
class StepResult:
    summary: str          # ce qui a été fait (affiché à l'utilisateur)
    feedback: str         # retour pour le LLM au tour suivant
    finished: bool = False
    answer: str = ""


class DesktopAgent(BaseAgent):
    name = "desktop"
    description = (
        "Agent bureau — voit l'écran (capture + modèle de vision, UI Automation, OCR), "
        "répond aux questions sur ce qui est affiché, propose de l'aide et agit à la souris "
        "et au clavier"
    )
    system_prompt = SYSTEM_PROMPT
    _auto_trace = False

    # ── LLM ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _timeout():
        import httpx

        from seraphim.settings import settings
        return httpx.Timeout(settings.desktop.llm_timeout, connect=10)

    @classmethod
    async def _ollama_decide(cls, model: str, base_url: str, messages: list[dict],
                             num_ctx: int, num_predict: int) -> str:
        """Appel Ollama dédié : sortie JSON contrainte, délai long (CPU lent)."""
        import httpx

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "format": DECISION_SCHEMA,
            "keep_alive": "30m",
            "options": {"temperature": 0.1, "num_ctx": num_ctx, "num_predict": num_predict},
        }
        async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=cls._timeout()) as c:
            r = await c.post("/api/chat", json=payload)
            r.raise_for_status()
            return r.json().get("message", {}).get("content", "") or ""

    @classmethod
    async def _ollama_stream(cls, model: str, base_url: str, messages: list[dict],
                             num_ctx: int) -> AsyncIterator[str]:
        import httpx

        payload = {
            "model": model, "messages": messages, "stream": True, "keep_alive": "30m",
            "options": {"temperature": 0.2, "num_ctx": num_ctx, "num_predict": 700},
        }
        async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=cls._timeout()) as c, \
                c.stream("POST", "/api/chat", json=payload) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                chunk = data.get("message", {}).get("content", "")
                if chunk:
                    yield chunk
                if data.get("done"):
                    break

    async def _decide(self, task: str, history: list[str], obs_text: str, shot=None) -> dict:
        from seraphim.settings import settings

        hist = "\n".join(history[-8:]) or "(aucune action pour l'instant)"
        user = (f"Demande : {task}\n\nHistorique :\n{hist}\n\n"
                f"État actuel de l'écran :\n{obs_text}\n\n")
        if shot is not None:
            user += f"La capture jointe fait {shot.width}×{shot.height} pixels.\n"
        user += "Quelle est la prochaine action ? Réponds en JSON."

        if shot is not None:
            messages = [{"role": "system", "content": SYSTEM_PROMPT_VISION},
                        {"role": "user", "content": user, "images": [shot.b64]}]
        else:
            messages = [{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user}]

        eng = self.engine
        cfg = settings.desktop
        last_err: Exception | None = None
        for _ in range(2):
            if shot is not None:
                raw = await self._ollama_decide(cfg.vision_model, settings.engine.base_url,
                                                messages, cfg.vision_num_ctx, 300)
            elif getattr(eng, "id", "") == "ollama":
                raw = await self._ollama_decide(
                    getattr(eng, "model", settings.engine.model),
                    getattr(eng, "base_url", settings.engine.base_url),
                    messages, settings.engine.context_window, 300)
            else:
                raw = await self._chat(messages)
            try:
                return parse_decision(raw)
            except ValueError as exc:
                last_err = exc
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": "Réponds uniquement avec l'objet JSON demandé."})
        raise last_err or ValueError("pas de décision")

    # ── Mode regard : répondre sur ce qui est à l'écran, proposer ────────────

    async def _extract_action(self, question: str, answer: str, vision: bool, cfg) -> str:
        """La réponse conseille une manipulation sans ligne ACTION : on en tire un ordre court."""
        import httpx

        from seraphim.settings import settings

        prompt = (
            f"Demande de l'utilisateur : {question}\nRéponse donnée : {answer}\n\n"
            "Si la réponse conseille une manipulation concrète à l'écran (cliquer, ouvrir, taper, fermer…), "
            "écris-la comme un ordre court à l'impératif tutoyé, avec le texte exact affiché de la cible, "
            "ex : « ouvre README.md » ou « clique sur Réessayer ». "
            'Sinon, action = "". Réponds en JSON : {"action": "..."}'
        )
        model = cfg.vision_model if vision else getattr(self.engine, "model", settings.engine.model)
        payload = {
            "model": model, "stream": False, "keep_alive": "30m",
            "format": {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]},
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.0, "num_predict": 60},
        }
        async with httpx.AsyncClient(base_url=settings.engine.base_url.rstrip("/"),
                                     timeout=self._timeout()) as c:
            r = await c.post("/api/chat", json=payload)
            r.raise_for_status()
            raw = r.json().get("message", {}).get("content", "") or "{}"
        action = str(json.loads(raw).get("action") or "").strip().strip(".")
        return action if 3 <= len(action) <= 150 else ""

    async def _look(self, question: str, vision: bool, cfg) -> AsyncIterator[str]:
        from seraphim.desktop import computer as pc
        from seraphim.desktop import pending
        from seraphim.settings import settings

        obs = await asyncio.to_thread(pc.observe, None, False, not vision)
        context = obs.to_prompt()
        if vision:
            shot = await asyncio.to_thread(pc.screenshot_for_model, obs.window_hwnd, None,
                                           cfg.vision_max_width)
            source = f"capture jointe de la fenêtre « {obs.window_title or 'bureau'} »"
            user = {"role": "user", "images": [shot.b64],
                    "content": f"{context}\n\nDemande de l'utilisateur : {question}"}
            stream = self._ollama_stream(cfg.vision_model, settings.engine.base_url,
                                         [{"role": "system", "content": LOOK_PROMPT.format(source=source)}, user],
                                         cfg.vision_num_ctx)
        else:
            txt = await asyncio.to_thread(pc.screen_text)
            source = "liste des éléments de la fenêtre et texte lu à l'écran (OCR)"
            msgs = [{"role": "system", "content": LOOK_PROMPT.format(source=source)},
                    {"role": "user", "content": f"{context}\n\nTexte à l'écran :\n{txt[:3000]}\n\n"
                                                f"Demande de l'utilisateur : {question}"}]

            async def _once():
                yield await self._chat(msgs)
            stream = _once()

        # Streame la réponse ligne par ligne, en retenant la ligne « ACTION: … »
        full, line, blank = "", "", False
        async for chunk in stream:
            full += chunk
            line += chunk
            while "\n" in line:
                head, line = line.split("\n", 1)
                if _ACTION_LINE_RE.match(head) or (blank and not head.strip()):
                    continue
                blank = not head.strip()
                yield head + "\n"
        if line.strip() and not _ACTION_LINE_RE.match(line):
            yield line

        answer, action = split_action_line(full)
        if not action and _SUGGESTS_ACTION_RE.search(answer):
            try:
                action = await self._extract_action(question, answer, vision, cfg)
            except Exception as exc:
                logger.debug("action extraction failed: %s", exc)
        action = action.rstrip(". ")
        if action:
            pending.set_pending(self._session_id, action, question)
            yield (f"\n\n👉 Je peux m'en occuper : **{action}**. "
                   "Réponds « oui » pour que je le fasse.")

    # ── Mode action : exécution d'une action ─────────────────────────────────

    @staticmethod
    def _bring_to_front(hwnd: int) -> None:
        """Remet la fenêtre de travail devant si l'utilisateur a cliqué ailleurs."""
        import win32gui

        from seraphim.desktop import computer as pc

        if win32gui.GetForegroundWindow() != hwnd:
            pc.focus_window(hwnd)

    async def _execute(self, d: dict, obs, task: str, cfg, shot=None) -> StepResult:
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

        if cfg.confirm_risky and action in ("click", "double_click", "click_at", "type", "key",
                                            "click_on", "open_item"):
            label = " ".join(filter(None, [el.name if el else "",
                                           text if action in ("key", "click_at", "click_on", "open_item") else ""]))
            fam = risky_family(label, task)
            if fam:
                msg = (f"Je me suis arrêté avant d'agir sur « {label} » (action sensible : {fam}). "
                       "Si c'est bien ce que tu veux, redemande-le explicitement.")
                return StepResult(summary=f"⛔ {msg}", feedback="", finished=True, answer=msg)

        if action in _INPUT_ACTIONS and obs.window_hwnd:
            await asyncio.to_thread(self._bring_to_front, obs.window_hwnd)

        if action in ("click_on", "open_item"):
            if not text:
                return StepResult(f"⚠️ {action} sans texte", "ERREUR : 'text' doit être le texte affiché de la cible.")
            out = await self._activate(text, "open" if action == "open_item" else "click", task, cfg,
                                       obs.window_hwnd)
            if out.success:
                self._target_hwnd = None      # suivre la fenêtre qui vient éventuellement de s'ouvrir
                return StepResult(out.message, out.message)
            if out.message.startswith("⛔"):
                return StepResult(out.message, "", finished=True, answer=out.message)
            return StepResult(f"⚠️ {out.message}",
                              f"ERREUR : {out.message} Essaie un autre texte visible, un [id] ou click_at.")

        if action in ("click", "double_click", "right_click"):
            if (err := need_el()):
                return err
            button = "right" if action == "right_click" else "left"
            await asyncio.to_thread(pc.click, el.x, el.y, button, action == "double_click")
            verb = {"click": "Clic", "double_click": "Double-clic", "right_click": "Clic droit"}[action]
            return StepResult(f"{verb} sur « {el.name} »", f"{verb} effectué sur [{el.id}] « {el.name} ».")

        if action == "click_at":
            x, y = d.get("x"), d.get("y")
            if shot is None or x is None or y is None:
                return StepResult("⚠️ click_at sans position",
                                  "ERREUR : click_at demande x ET y (pixels de la capture). "
                                  "Donne-les, ou utilise click avec un [id].")
            sx, sy = shot.to_screen(x, y)
            await asyncio.to_thread(pc.click, sx, sy)
            what = f" sur « {text} »" if text else ""
            return StepResult(f"Clic{what} (position {x},{y})", f"Clic effectué en ({x},{y}){what}.")

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

    async def _activate(self, target: str, mode: str, task: str, cfg, hwnd: int | None = None):
        """Trouve `target` n'importe où à l'écran et l'active (moteur générique)."""
        from seraphim.desktop import activate
        from seraphim.settings import settings

        def forbid(name: str) -> str | None:
            return risky_family(name, task) if cfg.confirm_risky else None

        return await activate.locate_and_activate(
            target, mode, hwnd,
            vision_model=cfg.vision_model if self._vision else "",
            base_url=settings.engine.base_url, forbid=forbid,
        )

    async def _direct(self, task: str, cfg) -> tuple[bool, str]:
        """Ordre simple « clique sur X » / « ouvre X » : trouver + activer, sans LLM.
        Retourne (réussi, compte rendu)."""
        parsed = parse_simple_order(task)
        if not parsed:
            return False, ""
        mode, target = parsed
        from seraphim.desktop.activate import clean_target

        name = clean_target(target)

        async def launch_app() -> str | None:
            from seraphim.skills.registry import SKILL_REGISTRY, discover_skills
            if not SKILL_REGISTRY:
                discover_skills()
            skill = SKILL_REGISTRY.get("app_manager")
            if skill:
                r = await skill.run(action="open", app=name)
                if r.success:
                    return r.output
            return None

        known_app = False
        if mode == "open":
            try:
                from seraphim.skills.system.apps import _APP_MAP
                known_app = name.lower() in _APP_MAP
            except Exception:
                pass
        if known_app and (msg := await launch_app()):
            return True, msg

        out = await self._activate(target, mode, task, cfg)
        if out.success or out.message.startswith("⛔"):
            return True, out.message
        if mode == "open" and not known_app and (msg := await launch_app()):
            return True, msg      # pas à l'écran : c'était une application à lancer

        # Introuvable : nom peut-être mal entendu par la dictée → on devine parmi ce
        # qui est visible, et on PROPOSE (l'utilisateur valide par « oui »).
        if out.candidate is None:
            guess = await self._guess(task, target, name, cfg)
            if guess:
                return True, guess
        return False, out.message

    async def _guess(self, task: str, target: str, name: str, cfg) -> str:
        """« Tu voulais dire « README.md » ? » + proposition en attente, ou ""."""
        from seraphim.desktop import activate, pending

        _, hint = activate.split_location(target)
        try:
            hwnd = await asyncio.to_thread(activate.window_for_hint, hint) if hint else None
            # « un fichier X » / « le dossier X » : seulement les éléments de liste
            items_only = bool(re.search(r"\b(?:fichiers?|dossiers?|files?|folders?)\b", task, re.I))
            names = await asyncio.to_thread(activate.visible_names, hwnd, 90, bool(hwnd), items_only)
            if items_only and not names:      # appli sans éléments de liste (ex : IDE) : tout
                names = await asyncio.to_thread(activate.visible_names, hwnd, 90, bool(hwnd), False)
            guess = activate.guess_misheard(name, names)
        except Exception as exc:
            logger.debug("misheard guess failed: %s", exc)
            return ""
        if not guess or activate.match_score(name, guess) >= 100:
            return ""
        mode = parse_simple_order(task)[0]
        verb = {"open": "ouvre", "double": "double-clique sur", "right": "fais un clic droit sur"}.get(mode, "clique sur")
        where = f" dans {hint}" if hint else ""
        pending.set_pending(self._session_id, f"{verb} « {guess} »{where}", task)
        infinitive = {"open": "l'ouvre", "double": "double-clique dessus",
                      "right": "fasse un clic droit dessus"}.get(mode, "clique dessus")
        return (f"Je ne vois pas « {name} » à l'écran. Tu voulais dire **« {guess} »** ? "
                f"Réponds « oui » pour que je {infinitive}.")

    async def _act(self, task: str, vision: bool, cfg) -> AsyncIterator[str]:
        from seraphim.desktop import computer as pc

        self._vision = vision
        self._target_hwnd: int | None = None
        history: list[str] = []
        last_sig: list[str] = []
        warned = False

        if parse_simple_order(task):
            yield "🖐️ Je prends la souris (souris dans le coin haut-gauche = arrêt d'urgence).\n"
            warned = True
            done, msg = await self._direct(task, cfg)
            if done:
                yield msg
                return
            yield f"{msg} Je cherche autrement…\n"
            history.append(f"Recherche directe : {msg}")

        for step in range(1, cfg.max_steps + 1):
            if await asyncio.to_thread(pc.emergency_stop_requested):
                yield "\n⛔ Arrêt d'urgence demandé (souris dans le coin haut-gauche)."
                return

            obs = await asyncio.to_thread(pc.observe, self._target_hwnd, False, not vision)
            self._target_hwnd = obs.window_hwnd or self._target_hwnd
            shot = None
            if vision:
                try:
                    shot = await asyncio.to_thread(
                        pc.screenshot_for_model, obs.window_hwnd, obs.elements, cfg.vision_max_width)
                except Exception as exc:
                    logger.warning("desktop screenshot failed: %s", exc)

            try:
                d = await self._decide(task, history, obs.to_prompt(), shot)
            except Exception as exc:
                logger.warning("desktop decide failed: %s", exc)
                yield f"\n❌ Le modèle n'a pas fourni d'action exploitable ({type(exc).__name__}: {exc})."
                return

            sig = f"{d.get('action')}|{d.get('target')}|{d.get('x')}|{d.get('y')}|{d.get('text')}"
            last_sig = (last_sig + [sig])[-3:]
            if len(last_sig) == 3 and len(set(last_sig)) == 1 and d.get("action") not in ("wait",):
                yield "\n❌ Je tourne en rond (même action répétée 3 fois) — j'arrête."
                return

            if await asyncio.to_thread(pc.emergency_stop_requested):
                yield "\n⛔ Arrêt d'urgence demandé (souris dans le coin haut-gauche)."
                return

            if d.get("action") in _INPUT_ACTIONS and not warned:
                warned = True
                yield "🖐️ Je prends la souris (souris dans le coin haut-gauche = arrêt d'urgence).\n"

            try:
                res = await self._execute(d, obs, task, cfg, shot)
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
                + (f" ({d.get('x')},{d.get('y')})" if d.get("action") == "click_at" else "")
                + (f" « {d.get('text')} »" if d.get("text") else "")
                + f" → {res.feedback}"
            )
            await asyncio.sleep(cfg.settle_delay)

        yield f"\n⚠️ Nombre maximal d'étapes atteint ({cfg.max_steps}) sans terminer la tâche."

    # ── Point d'entrée ───────────────────────────────────────────────────────

    async def steps(self, task: str, session_id: str | None = None) -> AsyncIterator[str]:
        if sys.platform != "win32":
            yield "L'agent bureau ne fonctionne que sous Windows."
            return
        try:
            from seraphim.desktop import computer as pc
            from seraphim.desktop import pending
        except ImportError as exc:
            yield f"Dépendance manquante pour l'agent bureau : {exc}. Lance `uv sync`."
            return
        from seraphim.settings import settings

        cfg = settings.desktop
        self._session_id = session_id

        # « oui » à une proposition faite au message précédent → on l'exécute
        prop = pending.pop_pending(session_id) if pending.is_confirmation(task) else None
        pending.clear_pending(session_id)
        if prop:
            task = prop.task
            yield f"✅ C'est parti : {task}\n"

        vision = await pc.vision_model_available(cfg.vision_model, settings.engine.base_url)
        if not vision:
            yield ("_(Je ne lis que le texte de l'écran — pour que je voie les images, installe un "
                   f"modèle de vision : `ollama pull {cfg.vision_model or 'qwen2.5vl:3b'}`.)_\n\n")

        if prop or is_order(task):
            async for chunk in self._act(task, vision, cfg):
                yield chunk
        else:
            async for chunk in self._look(task, vision, cfg):
                yield chunk

    async def stream(self, query: str, context: AgentContext | None = None):
        async for chunk in self.steps(query, context.session_id if context else None):
            yield chunk

    async def run(self, query: str, context: AgentContext | None = None) -> str:
        return "".join([c async for c in self.steps(query, context.session_id if context else None)])

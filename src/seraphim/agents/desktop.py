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
    - ordre simple (« clique sur X ») : trouvé et activé directement, sans modèle ;
    - sinon : le modèle texte écrit un PLAN en commandes simples, une par ligne
      (clique sur / clic droit sur / ouvre / écris / touche / lance / cherche / fini…),
      à partir de l'écran actuel (UI Automation + OCR). Le code exécute chaque étape
      avec le moteur générique desktop/activate.py et vérifie qu'elle a marché ;
      au premier échec, il regarde l'écran et demande un nouveau plan.

Sécurité :
  - arrêt d'urgence : placer la souris dans le coin supérieur gauche de l'écran
  - nombre d'étapes et de replanifications limité (settings.desktop)
  - arrêt avant un clic/une touche « supprimer / payer / envoyer… » non demandé
  - « lance » refuse les enchaînements et commandes destructrices
  - une étape qui échoue deux fois doit être remplacée par une autre approche
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
# « clique sur l'icône du disque D pour ouvrir ses propriétés » : un but, donc un plan
_PURPOSE_RE = re.compile(r"\b(?:pour|afin\s+de|afin\s+d['’]|in\s+order\s+to)\b", re.I)
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
    if not core or _CHAINED_RE.search(core) or _PURPOSE_RE.search(core) or len(core) > 80:
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


_SPLIT_ORDER_RE = re.compile(r"\s*(?:,|;|\bet\s+(?:ensuite\s+|apr[eè]s\s+)?|\bpuis\b|\bensuite\b)\s*", re.I)


# Un morceau qui commence ainsi n'est pas une nouvelle étape (« et tu vois dans… »)
_CONTINUATION_RE = re.compile(
    r"^(?:tu|vous|on|il|elle|ils|que|qu['’]|qui|[çc]a|c['’]est|ce|cet|cette|le|la|les|l['’]|un|une|des|du|de|d['’]|"
    r"dans|sur|à|au|aux|avec|en|par|pour|mon|ma|mes|ton|ta|tes|son|sa|ses|si|quand|comme)\b",
    re.I,
)


def split_order(query: str) -> list[str]:
    """« ouvre X et trie par Y » → ["ouvre X", "trie par Y"]. Les morceaux sans verbe
    d'action (« et tu vois dans l'explorateur ») restent collés au précédent."""
    parts: list[str] = []
    for chunk in _SPLIT_ORDER_RE.split((query or "").strip().rstrip(".!?")):
        chunk = chunk.strip()
        if not chunk:
            continue
        first = chunk.split()[0].lower()
        looks_like_verb = bool(re.fullmatch(r"[a-zàâçéèêëîïôûùüœ'’-]+(?:e|s|z)|va|vas", first))
        if parts and (_CONTINUATION_RE.match(chunk) or not looks_like_verb):
            parts[-1] += " et " + chunk
        else:
            parts.append(chunk)
    return parts


def is_order(query: str) -> bool:
    """Ordre explicite d'agir à l'écran (« clique sur… », « peux-tu ouvrir… »)."""
    return bool(_ORDER_RE.match(query or ""))


# ── Plan en étapes simples (mode action) ─────────────────────────────────────

PLAN_PROMPT = """Tu pilotes un PC Windows 11 en français pour l'utilisateur, à la souris et au clavier.
Tu écris un plan : une commande par ligne, rien d'autre (pas de numéros, pas d'explications).

Commandes :
clique sur NOM — NOM = texte affiché à l'écran (bouton, fichier, menu, onglet…)
double-clique sur NOM
clic droit sur NOM
ouvre NOM — fichier, dossier ou application
écris TEXTE — dans le champ actif
touche RACCOURCI — ex : win+e, alt+enter, ctrl+l, enter, escape, alt+f4
lance CIBLE — comme Exécuter (Win+R) : dossier (D:\\), page de paramètres (ms-settings:display), programme (taskmgr)
cherche TEXTE — recherche Windows (menu Démarrer) puis Entrée
défile vers le bas / défile vers le haut
attends
lis l'écran — pour lire un texte avant de répondre
fini : PHRASE — quand la demande est accomplie, ce que tu dis à l'utilisateur

Aide-mémoire Windows :
- Disques : lance shell:MyComputerFolder (Ce PC) ; un disque s'affiche « Nom (D:) ».
- Propriétés d'un fichier, dossier ou disque : clic droit sur NOM puis clique sur Propriétés.
- Paramètres : lance ms-settings:display (écran), ms-settings:sound (son), ms-settings:colors (couleurs, mode sombre),
  ms-settings:network (réseau), ms-settings:bluetooth, ms-settings:appsfeatures (applications),
  ms-settings:storagesense (stockage), ms-settings:windowsupdate (mises à jour), ms-settings:personalization.
- Outils : lance taskmgr (gestionnaire des tâches), control (panneau de configuration),
  devmgmt.msc (périphériques), diskmgmt.msc (gestion des disques), winver (version de Windows).
- Dossiers : lance D:\\ ; lance shell:Downloads ; lance shell:Desktop ; lance shell:Documents.
- Utilise les noms EXACTS affichés à l'écran (voir « Écran actuel »).

Exemple — Demande : ouvre les paramètres du son
lance ms-settings:sound
fini : J'ai ouvert les paramètres du son.

Exemple — Demande : renomme le fichier rapport.txt en final.txt
clic droit sur rapport.txt
clique sur Renommer
écris final.txt
touche enter
fini : Le fichier s'appelle maintenant final.txt."""

_STEP_RE = re.compile(
    r"^\s*(?:[-*•]|\d+\s*[.)-])?\s*"
    r"(?P<cmd>double[- ]?clique\s+sur|clic\s+droit\s+sur|clique\s+droit\s+sur|clique\s+sur|clic\s+sur|"
    r"ouvre|[ée]cris|tape|touches?|appuie\s+sur|lance|ex[ée]cute|cherche|"
    r"d[ée]file\s+vers\s+le\s+(?:bas|haut)|d[ée]file|attends|lis\s+l['’]?\s*[ée]cran|fini|termin[ée])"
    r"\s*:?\s*(?P<arg>.*?)\s*$",
    re.I,
)


@dataclass
class PlanStep:
    kind: str        # click | double | right | open | type | key | run | search | scroll | wait | read | done
    arg: str
    raw: str


def parse_plan(text: str) -> list[PlanStep]:
    """Lignes du plan écrit par le modèle → étapes. Les lignes hors format sont ignorées."""
    steps: list[PlanStep] = []
    for line in (text or "").splitlines():
        line = line.strip().strip("`")
        m = _STEP_RE.match(line)
        if not m:
            continue
        cmd = re.sub(r"\s+", " ", m.group("cmd").lower())
        arg = m.group("arg").strip()
        if len(arg) > 1 and arg[0] in "«\"'“" and arg[-1] in "»\"'”":
            arg = arg[1:-1].strip()
        if cmd.startswith(("fini", "termin")):
            if normalize_done := arg.lower().strip(" .!:"):
                arg = "" if normalize_done in ("fini", "terminé", "termine", "c'est fini") else arg
            steps.append(PlanStep("done", arg, line))
            break
        if cmd.startswith(("double",)):
            kind = "double"
        elif "droit" in cmd:
            kind = "right"
        elif cmd.startswith(("clique", "clic")):
            kind = "click"
        elif cmd == "ouvre":
            kind = "open"
        elif cmd.startswith(("écris", "ecris", "tape")):
            kind = "type"
        elif cmd.startswith(("touche", "appuie")):
            kind = "key"
            arg = re.sub(r"\s*\+\s*", "+", arg.lower()).rstrip(".")
        elif cmd in ("lance", "exécute", "execute"):
            kind = "run"
            arg = arg.rstrip(".")
        elif cmd == "cherche":
            kind = "search"
        elif cmd.startswith(("défile", "defile")):
            kind, arg = "scroll", ("up" if "haut" in cmd else "down")
        elif cmd == "attends":
            kind = "wait"
        elif cmd.startswith("lis"):
            kind = "read"
        else:
            continue
        if kind in ("click", "double", "right", "open", "type", "key", "run", "search") and not arg:
            continue
        steps.append(PlanStep(kind, arg, line))
    return steps


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


class DesktopAgent(BaseAgent):
    name = "desktop"
    description = (
        "Agent bureau — voit l'écran (capture + modèle de vision, UI Automation, OCR), "
        "répond aux questions sur ce qui est affiché, propose de l'aide et agit à la souris "
        "et au clavier"
    )
    system_prompt = PLAN_PROMPT
    _auto_trace = False

    # ── LLM ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _timeout():
        import httpx

        from seraphim.settings import settings
        return httpx.Timeout(settings.desktop.llm_timeout, connect=10)

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

    @staticmethod
    async def _launch_app(name: str) -> str | None:
        from seraphim.skills.registry import SKILL_REGISTRY, discover_skills
        if not SKILL_REGISTRY:
            discover_skills()
        skill = SKILL_REGISTRY.get("app_manager")
        if skill:
            r = await skill.run(action="open", app=name)
            if r.success:
                await asyncio.sleep(2.0)
                return r.output
        return None

    async def _open_elsewhere(self, name: str) -> str | None:
        """« ouvre X » quand X n'est pas à l'écran : lieu Windows connu (paramètres,
        dossiers, outils), sinon application installée. None si rien ne marche."""
        from seraphim.desktop import computer as pc
        from seraphim.desktop.places import resolve_place

        if place := resolve_place(name):
            try:
                await asyncio.to_thread(pc.run_target, place)
                await asyncio.sleep(2.0)
                return f"J'ai ouvert « {name} » ({place})."
            except Exception as exc:
                logger.debug("open place %s failed: %s", place, exc)
        return await self._launch_app(name)

    async def _direct(self, task: str, cfg) -> tuple[bool, str]:
        """Ordre simple « clique sur X » / « ouvre X » : trouver + activer, sans LLM.
        Retourne (réussi, compte rendu)."""
        parsed = parse_simple_order(task)
        if not parsed:
            return False, ""
        mode, target = parsed
        from seraphim.desktop.activate import clean_target

        name = clean_target(target)

        known_app = False
        if mode == "open":
            try:
                from seraphim.skills.system.apps import _APP_MAP
                known_app = name.lower() in _APP_MAP
            except Exception:
                pass
        if known_app and (msg := await self._launch_app(name)):
            return True, msg

        out = await self._activate(target, mode, task, cfg)
        if out.success or out.message.startswith("⛔"):
            return True, out.message
        if mode == "open" and not known_app and (msg := await self._open_elsewhere(name)):
            return True, msg      # pas à l'écran : un lieu Windows ou une application

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

    # ── Mode action : planifier, exécuter, replanifier ───────────────────────

    async def _plan(self, goal: str, history: list[str], obs_text: str, problem: str) -> list[PlanStep]:
        done = "\n".join(f"{i}. {h}" for i, h in enumerate(history[-12:], 1)) or "(rien pour l'instant)"
        user = (f"Demande : {goal}\n\nDéjà fait :\n{done}\n\nÉcran actuel :\n{obs_text}\n\n"
                + (f"Problème à l'étape précédente : {problem}\n\n" if problem else "")
                + "Écris la suite du plan, une commande par ligne "
                  "(ou seulement « fini : … » si la demande est déjà accomplie).")
        raw = await self._chat([{"role": "system", "content": PLAN_PROMPT}, {"role": "user", "content": user}])
        return parse_plan(raw)

    async def _focus_work_window(self) -> None:
        """Les touches et la saisie doivent aller dans la fenêtre de travail, pas dans Seraphim."""
        import win32gui

        from seraphim.desktop import activate
        from seraphim.desktop import computer as pc

        fg = win32gui.GetForegroundWindow()
        if fg and activate._is_seraphim(fg):
            hwnd, _, _ = await asyncio.to_thread(pc.pick_target_window)
            if hwnd:
                await asyncio.to_thread(pc.focus_window, hwnd)

    async def _run_step(self, step: PlanStep, goal: str, cfg) -> tuple[bool, str]:
        from seraphim.desktop import computer as pc

        kind, arg = step.kind, step.arg
        if kind in ("click", "double", "right", "open"):
            out = await self._activate(arg, kind, goal, cfg)
            if out.success or out.message.startswith("⛔") or kind != "open":
                return out.success, out.message
            from seraphim.desktop.activate import clean_target
            if msg := await self._open_elsewhere(clean_target(arg)):
                return True, msg
            return False, out.message

        if kind == "run":
            try:
                await asyncio.to_thread(pc.run_target, arg)
            except Exception as exc:
                return False, f"impossible de lancer « {arg} » : {exc}"
            await asyncio.sleep(2.0)
            return True, f"« {arg} » lancé"

        if kind == "search":
            await asyncio.to_thread(pc.press_keys, "win")
            await asyncio.sleep(0.9)
            await asyncio.to_thread(pc.type_text, arg)
            await asyncio.sleep(1.5)
            await asyncio.to_thread(pc.press_keys, "enter")
            await asyncio.sleep(1.5)
            return True, f"recherche Windows « {arg} »"

        if kind in ("key", "type", "scroll"):
            await self._focus_work_window()
            if kind == "key":
                try:
                    pc.keys_to_pywinauto(arg)
                except ValueError as exc:
                    return False, f"raccourci invalide « {arg} » ({exc})"
                if cfg.confirm_risky and (fam := risky_family(arg, goal)):
                    return False, f"⛔ touche « {arg} » non envoyée (action sensible : {fam})"
                await asyncio.to_thread(pc.press_keys, arg)
                return True, f"touches « {arg} »"
            if kind == "type":
                await asyncio.to_thread(pc.type_text, arg)
                return True, "texte saisi"
            import win32gui
            x, y = pc.window_center(win32gui.GetForegroundWindow())
            await asyncio.to_thread(pc.scroll, x, y, "up" if arg == "up" else "down")
            return True, "défilement"

        if kind == "wait":
            await asyncio.sleep(2.0)
            return True, "attente"

        if kind == "read":
            txt = await asyncio.to_thread(pc.screen_text)
            return True, "texte à l'écran : " + (txt[:1500] or "(aucun)")

        return False, f"commande inconnue « {step.raw} »"

    async def _act(self, task: str, vision: bool, cfg, goal: str = "") -> AsyncIterator[str]:
        """Ordre simple → exécution directe. Sinon : plan en étapes simples, exécutées
        une à une avec vérification ; au premier échec, on regarde l'écran et on
        replanifie (le modèle voit ce qui a marché, ce qui a échoué, et l'écran)."""
        from seraphim.desktop import computer as pc

        goal = goal or task
        self._vision = vision
        await asyncio.to_thread(pc.release_emergency_corner)
        yield "🖐️ Je prends la main (souris dans le coin haut-gauche = arrêt d'urgence).\n"

        history: list[str] = []
        problem = ""
        if parse_simple_order(task):
            done, msg = await self._direct(task, cfg)
            if done:
                yield msg
                return
            history.append(f"{task} → ÉCHEC : {msg}")
            problem = msg
            yield f"{msg} Je cherche un autre moyen…\n"
        else:
            # « ouvre le gestionnaire des tâches et trie par mémoire » : les premières
            # parties simples sont faites directement, le plan s'occupe du reste.
            parts = split_order(task)
            for part in parts[:-1] if len(parts) > 1 else []:
                if not parse_simple_order(part):
                    break
                done, msg = await self._direct(part, cfg)
                yield f"• {part} {'✓' if done else '✗ ' + msg}\n"
                history.append(f"{part} → " + ("OK" if done else f"ÉCHEC : {msg}"))
                if not done:
                    problem = msg
                    break
                await asyncio.sleep(cfg.settle_delay)

        n = 0
        failures: dict[str, int] = {}
        successes: dict[str, int] = {}
        for _ in range(cfg.max_replans):
            obs = await asyncio.to_thread(pc.observe, None, False, True)
            try:
                plan = await self._plan(goal, history, obs.to_prompt(), problem)
            except Exception as exc:
                logger.warning("desktop plan failed: %s", exc)
                yield f"\n❌ Je n'arrive pas à établir un plan ({type(exc).__name__}: {exc})."
                return
            if not plan:
                problem = "ta réponse ne contenait aucune commande valide : une commande par ligne."
                continue

            problem = ""
            for step in plan:
                if step.kind == "done":
                    yield f"\n{step.arg or 'C’est fait.'}"
                    return
                if await asyncio.to_thread(pc.emergency_stop_requested):
                    yield "\n⛔ Arrêt d'urgence demandé (souris dans le coin haut-gauche)."
                    return
                if n >= cfg.max_steps:
                    yield f"\n⚠️ J'ai atteint la limite de {cfg.max_steps} étapes sans finir."
                    return
                key = step.raw.lower()
                if step.kind not in ("scroll", "key", "wait", "type", "read") and successes.get(key, 0) >= (
                        1 if history and history[-1].lower() == f"{key} → ok" else 2):
                    problem = (f"« {step.raw} » a déjà été fait avec succès : ne le refais pas. Regarde "
                               "l'écran actuel et écris l'étape SUIVANTE (ou « fini : … » si c'est accompli).")
                    break
                n += 1
                try:
                    ok, msg = await self._run_step(step, goal, cfg)
                except Exception as exc:
                    logger.exception("desktop step failed")
                    ok, msg = False, f"erreur : {exc}"
                yield f"{n}. {step.raw} {'✓' if ok else '✗ ' + msg}\n"
                if msg.startswith("⛔"):
                    yield f"\n{msg}. Si c'est bien ce que tu veux, demande-le explicitement."
                    return
                history.append(f"{step.raw} → " + ("OK" if ok else f"ÉCHEC : {msg}"))
                if ok:
                    successes[key] = successes.get(key, 0) + 1
                else:
                    failures[key] = failures.get(key, 0) + 1
                    if failures[key] >= 2:
                        problem = (f"« {step.raw} » a déjà échoué {failures[key]} fois : "
                                   "fais autrement (autre nom visible à l'écran, raccourci, lance, cherche).")
                    else:
                        problem = msg
                    break
                await asyncio.sleep(cfg.settle_delay)
            # Plan terminé sans « fini », ou étape ratée : on regarde l'écran et on continue

        yield "\n⚠️ Je n'ai pas réussi à aller au bout. " + (f"Dernier problème : {problem}" if problem else "")

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
        goal = task
        if prop:
            task, goal = prop.task, prop.question or prop.task   # objectif = la demande d'origine
            yield f"✅ C'est parti : {task}\n"

        vision = await pc.vision_model_available(cfg.vision_model, settings.engine.base_url)
        if not vision:
            yield ("_(Je ne lis que le texte de l'écran — pour que je voie les images, installe un "
                   f"modèle de vision : `ollama pull {cfg.vision_model or 'qwen2.5vl:3b'}`.)_\n\n")

        if prop or is_order(task):
            async for chunk in self._act(task, vision, cfg, goal):
                yield chunk
        else:
            async for chunk in self._look(task, vision, cfg):
                yield chunk

    async def stream(self, query: str, context: AgentContext | None = None):
        async for chunk in self.steps(query, context.session_id if context else None):
            yield chunk

    async def run(self, query: str, context: AgentContext | None = None) -> str:
        return "".join([c async for c in self.steps(query, context.session_id if context else None)])

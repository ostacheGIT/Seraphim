"""
Auto-router — sélectionne automatiquement l'agent optimal selon la requête.
Aucune intervention utilisateur requise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from seraphim.agents.verification import is_user_correction

# ── Patterns de détection ──────────────────────────────────────────────────────

_MATH_RE = re.compile(
    r"""^(?:
        (?:combien\s+(?:font|fait|vaut|égal(?:e)?)\s+)?
        (?:calcule?\s+|compute\s+|évalue\s+)?
    )?
    (?P<expr>
        [\d\s\+\-\*\/\%\(\)\.\,\^]+
        |(?:sqrt|sin|cos|tan|log|abs|round|min|max|pi|e)\b.*
    )
    [?!.\s]*$""",
    re.I | re.VERBOSE,
    )

_MATH_FNS = {"sqrt", "sin", "cos", "tan", "log", "abs", "round", "min", "max", "pi"}

_SYSTEM_RE = re.compile(
    r"(?:ouvre|lance|démarre|open|start|volume|son|luminosité|brightness|"
    r"verrouille|lock|éteins?|shutdown|redémarre|restart|sleep|veille|"
    r"ram\b|cpu\b|gpu\b|mémoire\b|memory\b|batterie\b|battery\b|"
    r"(?:infos?\s+(?:pc|système|system|ram|cpu|gpu|ordi))|"
    r"(?:état\s+(?:du\s+)?(?:système|pc|ordi))|"
    r"(?:espace\s+disque|disk\s+space)|"
    r"(?:processus|process(?:es)?)\b|"
    r"(?:réseau|wifi|adresse\s+ip)|"
    r"(?:performances?\s+(?:pc|système))|"
    r"install[eé][es]?\b|logiciel\b|"
    r"(?:applications?|programmes?|apps?)\s+install|"
    r"(?:liste(?:r)?\s+(?:les\s+)?(?:applications?|logiciels?|programmes?))|"
    r"diagnostic\b|uptime\b)\b|"
    r"^(?:applications?|logiciels?|programmes?|apps?|software)\s*[?!.]?\s*$",
    re.I,
)

_CODE_RE = re.compile(
    r"""(?:
        (?:écris?|génère|crée|create|write|generate)\s+(?:un\s+)?(?:script|code|programme|function|fichier\s+py)|
        (?:exécute|run|lance|execute)\s+(?:ce\s+)?(?:code|script|python)|
        (?:debug|débugge|corrige|fix)\s+(?:ce\s+)?(?:code|script|erreur)|
        (?:implément|implement)|
        def\s+\w+\s*\(|
        import\s+\w+|
        ```python
    )""",
    re.I | re.VERBOSE,
    )

_CODEACT_RE = re.compile(
    r"""(?:
        (?:résous?|solve|trouve|find)\s+.{0,50}(?:en\s+python|avec\s+(?:du\s+)?code|en\s+code)|
        (?:écris?\s+(?:un\s+)?(?:script|code|programme).{0,40}et\s+(?:exécute|lance|run|teste?))|
        (?:génère.{0,30}(?:et\s+)?(?:exécute|lance|teste?))|
        (?:itère|iterate).{0,40}(?:code|script)|
        (?:analyse|traite|process)\s+.{0,50}(?:données|data|fichier|csv|json).{0,30}(?:python|code)|
        (?:calcule?\s+.{0,40}(?:et\s+)?(?:vérifie|check|teste?|affiche\s+le\s+résultat))
    )""",
    re.I | re.VERBOSE,
)

_HTTP_RE = re.compile(
    r"""(?:
        (?:requête|request|fetch|curl|appel\s+api|http\s+request)\s+(?:GET|POST|PUT|DELETE|PATCH|sur\s+)?https?://|
        (?:GET|POST|PUT|DELETE|PATCH)\s+https?://|
        (?:fais|make|send)\s+(?:une\s+)?(?:requête|request)\s+(?:GET|POST|PUT|DELETE|PATCH)
    )""",
    re.I | re.VERBOSE,
)

_WEB_RE = re.compile(
    r"""(?:
        (?:cherche|recherche|search|trouve|find|googl|bing)\s+|
        (?:quoi\s+de\s+neuf|what.s\s+new|actualité|news|dernier|latest|récent|recent)\b|
        (?:prix\s+(?:de|du|d.un)|price\s+of)\b|
        (?:météo|weather|température|temperature)\b|
        (?:qui\s+est|qu.est.ce\s+que|what\s+is|who\s+is)\s+.{0,40}(?:aujourd.hui|today|2024|2025|2026)\b|
        https?://\S+
    )""",
    re.I | re.VERBOSE,
    )

_FILE_RE = re.compile(
    r"""(?:
        (?:lis|lit|ouvre|lire|read)\s+(?:le\s+fichier|le\s+doc|le\s+document|ce\s+fichier)?\s*["\']?[\w\-\.\/\\~]+\.[a-z]{2,4}|
        (?:écris?|sauvegarde|enregistre|écrit|write|save)\s+(?:dans|to|dans\s+le\s+fichier)?\s*["\']?[\w\-\.\/\\~]+\.[a-z]{2,4}|
        (?:liste|list|affiche|montre|show)\s+(?:les\s+)?(?:fichiers?|dossiers?|files?|directories)\b|
        ~/|C:\\\\|D:\\\\|/home/|/etc/
    )""",
    re.I | re.VERBOSE,
    )

_MEMORY_RE = re.compile(
    r"""(?:
        (?:souviens?-?toi|remember|mémorise|note\s+que|retiens?)\b|
        (?:qu.est-ce\s+que\s+tu\s+sais|what\s+do\s+you\s+know|qu.as-tu\s+retenu)\b|
        (?:dans\s+notre\s+(?:dernière|précédente)\s+(?:conversation|discussion))\b
    )""",
    re.I | re.VERBOSE,
    )

_DEEP_RESEARCH_RE = re.compile(
    r"""(?:
        recherche\s+(?:approfondie|détaillée|complète|exhaustive)|
        deep[- ]research|comprehensive\s+research|thorough\s+research|
        analyse\s+(?:complète|approfondie|détaillée|exhaustive)|
        rapport\s+(?:complet\s+)?(?:sur|about)\b|
        état\s+de\s+l.art|state\s+of\s+the\s+art|
        compare(?:r)?\s+.{5,60}(?:en\s+détail|thoroughly|exhaustivement)|
        investigate\s+(?:thoroughly|in\s+depth)|
        enquête\s+(?:approfondie\s+)?sur\b|
        synthèse\s+(?:complète\s+)?(?:sur|de|des)\b
    )""",
    re.I | re.VERBOSE,
)

_THINK_RE = re.compile(
    r"""(?:
        (?:réfléchis|pense|analyse|évalue|compare|explique\s+(?:pourquoi|comment)|
        explain\s+(?:why|how)|think\s+about|raisonne|quel\s+est\s+le\s+meilleur|
        what.s\s+(?:the\s+best|better)|pros?\s+(?:et|and)\s+cons?|avantages?\s+(?:et|and)\s+inconv)
    )""",
    re.I | re.VERBOSE,
    )

# Reclassification / apprentissage de catégories
_RECLASSIFY_RE = re.compile(
    r"(?:"
    r"il\s+manque\b|"
    r"(?:ajoute[rz]?|rajoute[rz]?|mets?|add)\s+\S.{0,40}(?:dans|in|to|à|au|en)\s|"
    r"(?:mal\s+classé[e]?|mauvaise\s+catégorie|wrong\s+category)|"
    r"(?:reclassif|déplace[rz]?\s+\S.{0,40}(?:dans|vers))|"
    r"(?:catégorie[sz]?\s+personnalis)"
    r")",
    re.I,
)


@dataclass
class RoutingDecision:
    agent: str          # ex: "react", "chat", "skill:web_search"
    skill: str | None   # skill explicite si agent = "skill:xxx"
    reason: str         # explication lisible (debug)


def route(query: str) -> RoutingDecision:
    """
    Analyse la requête et retourne l'agent + skill optimal.
    Ordre de priorité : system > code > files > math > web > memory > think > chat
    """
    q = query.strip()

    # 0. Reclassification d'apps → react (a accès au skill installed_apps)
    if _RECLASSIFY_RE.search(q):
        return RoutingDecision(agent="react", skill=None, reason="app category learning request")

    # 0b. User explicitly correcting a wrong answer → researcher for deeper analysis
    if is_user_correction(q):
        return RoutingDecision(agent="researcher", skill=None, reason="user correction — deeper analysis")

    # 1. Commandes système directes → react agent (il a les DIRECT_PATTERNS)
    if _SYSTEM_RE.search(q):
        return RoutingDecision(agent="react", skill=None, reason="system command detected")

    # 2. CodeAct — iterative code generation + execution loop
    if _CODEACT_RE.search(q):
        return RoutingDecision(agent="codeact", skill=None, reason="iterative code execution detected")

    # 3. Code / script Python — "écris/génère" → coder; "exécute/run" → codeact (loop)
    if _CODE_RE.search(q):
        if re.search(r"(?:exécute|run|execute|lance)\s+(?:ce\s+)?(?:code|script)", q, re.I):
            return RoutingDecision(
                agent="codeact",
                skill=None,
                reason="code execution request — iterative CodeAct loop",
            )
        return RoutingDecision(agent="coder", skill=None, reason="code generation request detected")

    # 4. Fichiers locaux
    if _FILE_RE.search(q):
        return RoutingDecision(agent="react", skill=None, reason="file operation detected")

    # 4. Calcul mathématique
    m = _MATH_RE.match(q)
    if m:
        expr = m.group("expr").strip()
        words = re.findall(r"[a-zA-Z]{3,}", expr)
        if all(w.lower() in _MATH_FNS for w in words):
            return RoutingDecision(
                agent="skill:calculator",
                skill="calculator",
                reason="pure math expression detected",
            )

    # 5a. Requête HTTP explicite — avant web search (URL seule ≠ requête HTTP)
    if _HTTP_RE.search(q):
        return RoutingDecision(agent="react", skill=None, reason="HTTP request detected")

    # 5b. Recherche web
    if _WEB_RE.search(q):
        return RoutingDecision(
            agent="skill:web_search",
            skill="web_search",
            reason="web search intent detected",
        )

    # 6. Mémoire / contexte long terme
    if _MEMORY_RE.search(q):
        return RoutingDecision(agent="chat", skill=None, reason="memory/recall intent")

    # 7a. Deep research — multi-hop search with citations
    if _DEEP_RESEARCH_RE.search(q):
        return RoutingDecision(agent="deep_research", skill=None, reason="deep research intent detected")

    # 7b. Raisonnement complexe → researcher
    if _THINK_RE.search(q) or len(q.split()) > 30:
        return RoutingDecision(
            agent="researcher",
            skill=None,
            reason="complex reasoning/analysis detected",
        )

    # 8. Fallback — conversation générale
    return RoutingDecision(agent="chat", skill=None, reason="general conversation")
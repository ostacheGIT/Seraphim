"""
Auto-router — sélectionne automatiquement l'agent optimal selon la requête.
Aucune intervention utilisateur requise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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
    r"verrouille|lock|éteins?|shutdown|redémarre|restart|sleep|veille)\b",
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

_THINK_RE = re.compile(
    r"""(?:
        (?:réfléchis|pense|analyse|évalue|compare|explique\s+(?:pourquoi|comment)|
        explain\s+(?:why|how)|think\s+about|raisonne|quel\s+est\s+le\s+meilleur|
        what.s\s+(?:the\s+best|better)|pros?\s+(?:et|and)\s+cons?|avantages?\s+(?:et|and)\s+inconv)
    )""",
    re.I | re.VERBOSE,
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

    # 1. Commandes système directes → react agent (il a les DIRECT_PATTERNS)
    if _SYSTEM_RE.search(q):
        return RoutingDecision(agent="react", skill=None, reason="system command detected")

    # 2. Code / script Python
    if _CODE_RE.search(q):
        return RoutingDecision(
            agent="skill:code_interpreter",
            skill="code_interpreter",
            reason="code/script request detected",
        )

    # 3. Fichiers locaux
    if _FILE_RE.search(q):
        return RoutingDecision(agent="react", skill=None, reason="file operation detected")

    # 4. Calcul mathématique
    m = _MATH_RE.match(q)
    if m:
        expr = m.group("expr").strip()
        words = re.findall(r"[a-zA-Z]{3,}", expr)
        if not any(w.lower() not in _MATH_FNS for w in words):
            return RoutingDecision(
                agent="skill:calculator",
                skill="calculator",
                reason="pure math expression detected",
            )

    # 5. Recherche web
    if _WEB_RE.search(q):
        return RoutingDecision(
            agent="skill:web_search",
            skill="web_search",
            reason="web search intent detected",
        )

    # 6. Mémoire / contexte long terme
    if _MEMORY_RE.search(q):
        return RoutingDecision(agent="chat", skill=None, reason="memory/recall intent")

    # 7. Raisonnement complexe → think + chat
    if _THINK_RE.search(q) or len(q.split()) > 30:
        return RoutingDecision(
            agent="skill:think",
            skill="think",
            reason="complex reasoning/analysis detected",
        )

    # 8. Fallback — conversation générale
    return RoutingDecision(agent="chat", skill=None, reason="general conversation")
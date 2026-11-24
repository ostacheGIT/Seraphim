"""Actions proposées par l'agent bureau, en attente de validation de l'utilisateur.

Quand l'agent remarque quelque chose à l'écran sans en avoir reçu l'ordre explicite,
il propose une action au lieu de la faire. Si le message suivant de l'utilisateur
est une validation (« oui », « vas-y »…), l'agent exécute la proposition.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

TTL_S = 15 * 60          # une proposition expire après 15 minutes

_YES_RE = re.compile(
    r"^\s*(?:oui|ouais|ouep|yes|yep|ok(?:ay)?|d['’]?accord|vas[- ]y|go|"
    r"fais[- ]le|fais[- ]?(?:ça|ca)|c['’]?est\s+bon|valide|je\s+valide|confirme|"
    r"allez|bien\s+s[uû]r|carr[ée]ment|parfait|exactement)\b"
    r"[\s!.,'’-]*(?:\w+[\s!.,'’-]*){0,4}$",
    re.I,
)


@dataclass
class Proposal:
    task: str             # ce qu'il faut faire, formulé comme un ordre
    question: str         # la demande d'origine de l'utilisateur
    created: float


_pending: dict[str, Proposal] = {}


def _key(session_id: str | None) -> str:
    return session_id or "_"


def set_pending(session_id: str | None, task: str, question: str) -> None:
    _pending[_key(session_id)] = Proposal(task=task, question=question, created=time.time())


def get_pending(session_id: str | None) -> Proposal | None:
    p = _pending.get(_key(session_id))
    if p and time.time() - p.created > TTL_S:
        _pending.pop(_key(session_id), None)
        return None
    return p


def pop_pending(session_id: str | None) -> Proposal | None:
    p = get_pending(session_id)
    _pending.pop(_key(session_id), None)
    return p


def clear_pending(session_id: str | None) -> None:
    _pending.pop(_key(session_id), None)


_RESERVE_RE = re.compile(r"\b(?:mais|plut[ôo]t|non|pas|sauf|attends?|annule|stop|but|don['’]?t|no)\b", re.I)


def is_confirmation(text: str) -> bool:
    """« oui », « vas-y », « ok fais-le »… (message court d'accord, sans réserve)."""
    text = text or ""
    return bool(_YES_RE.match(text)) and not _RESERVE_RE.search(text)

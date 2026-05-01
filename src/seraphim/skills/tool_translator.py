"""ToolTranslator — traduit les noms d'outils externes vers les équivalents Seraphim.

Les bibliothèques de skills externes (Hermes Agent, OpenClaw) référencent les outils
par les noms standard de Claude Code (Bash, Read, Write, etc.). Seraphim utilise
ses propres noms. Ce module traduit ces références dans les corps markdown des skills.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Table de traduction — nom externe → nom Seraphim
# ---------------------------------------------------------------------------

TOOL_TRANSLATION: Dict[str, str] = {
    "Bash": "shell_exec",
    "Read": "file_read",
    "Write": "file_write",
    "Edit": "file_edit",
    "Glob": "file_glob",
    "Grep": "file_grep",
    "WebFetch": "web_search",
    "WebSearch": "web_search",
    "Task": "delegate_agent",
    "NotebookEdit": "notebook_edit",
}


class ToolTranslator:
    """Réécrit les références d'outils dans les corps markdown et les champs allowed-tools.

    Utilise un regex avec word-boundary pour éviter les correspondances partielles
    comme 'Reader' ou 'Reading'.
    """

    def __init__(
            self,
            translation_table: Dict[str, str] | None = None,
    ) -> None:
        self._table = dict(translation_table or TOOL_TRANSLATION)
        names = sorted(self._table.keys(), key=len, reverse=True)
        if names:
            self._pattern = re.compile(
                r"\b(" + "|".join(re.escape(n) for n in names) + r")\b"
            )
        else:
            self._pattern = None

    def translate_markdown(self, body: str) -> Tuple[str, List[str]]:
        """Traduit les références d'outils dans un corps markdown.

        Retourne
        --------
        tuple[str, list[str]]
            (corps réécrit, liste des noms d'outils non traduits trouvés)
        """
        if not body or self._pattern is None:
            return body, []

        def _sub(match: re.Match) -> str:
            return self._table.get(match.group(1), match.group(1))

        new_body = self._pattern.sub(_sub, body)

        untranslated: List[str] = []
        candidate_pattern = re.compile(r"\b([A-Z][a-z]+[A-Z][a-zA-Z]*)(?:\s+tool|\b)")
        for cand in candidate_pattern.findall(body):
            if cand not in self._table and cand not in untranslated:
                if 3 <= len(cand) <= 30:
                    untranslated.append(cand)
        return new_body, untranslated

    def translate_allowed_tools(self, allowed: str) -> Tuple[str, List[str]]:
        """Traduit le champ allowed-tools délimité par des espaces.

        Les tokens peuvent avoir des arguments entre parenthèses comme ``Bash(git:*)``.
        Seul le préfixe avant le premier ``(`` est traduit.
        """
        if not allowed:
            return allowed, []

        out_tokens: List[str] = []
        untranslated: List[str] = []
        for token in allowed.split():
            if "(" in token:
                head, _, tail = token.partition("(")
                tail = "(" + tail
            else:
                head, tail = token, ""

            if head in self._table:
                out_tokens.append(self._table[head] + tail)
            else:
                out_tokens.append(token)
                if head not in untranslated:
                    untranslated.append(head)

        return " ".join(out_tokens), untranslated


__all__ = ["TOOL_TRANSLATION", "ToolTranslator"]

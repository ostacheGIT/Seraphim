import asyncio
import logging

from seraphim.skills.base import BaseSkill, SkillResult
from ddgs import DDGS

logger = logging.getLogger(__name__)


def _ddgs_search(query: str, max_results: int) -> list[dict]:
    """Blocking DDGS call — must run in executor, not in the event loop."""
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results))


class WebSearchSkill(BaseSkill):
    name        = "web_search"
    description = "Recherche sur internet via DuckDuckGo et retourne les meilleurs résultats."
    parameters  = {
        "type": "object",
        "properties": {
            "query":       {"type": "string",  "description": "La requête de recherche"},
            "max_results": {"type": "integer", "description": "Nombre de résultats (défaut: 5)", "default": 5}
        },
        "required": ["query"]
    }

    async def run(self, query: str, max_results: int = 5, **kwargs) -> SkillResult:
        try:
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(None, _ddgs_search, query, max_results)

            if not results:
                return SkillResult(success=False, output="Aucun résultat trouvé.", error="empty")

            lines = []
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. **{r.get('title', 'Sans titre')}**")
                lines.append(f"   {r.get('body', '')}")
                lines.append(f"   {r.get('href', '')}")

            return SkillResult(success=True, output="\n".join(lines))

        except Exception as e:
            logger.warning("web_search failed for %r: %s", query, e)
            return SkillResult(success=False, output="Erreur de recherche web.", error=str(e))
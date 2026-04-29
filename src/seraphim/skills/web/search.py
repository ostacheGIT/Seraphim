from seraphim.skills.base import BaseSkill, SkillResult
from ddgs import DDGS


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
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))

            if not results:
                return SkillResult(success=False, output="Aucun résultat trouvé.", error="empty")

            lines = []
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. **{r.get('title', 'Sans titre')}**")
                lines.append(f"   {r.get('body', '')}")
                lines.append(f"   {r.get('href', '')}")

            return SkillResult(success=True, output="\n".join(lines))

        except Exception as e:
            return SkillResult(success=False, output="Erreur de recherche web.", error=str(e))
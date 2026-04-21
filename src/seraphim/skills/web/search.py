import httpx
from seraphim.skills.base import BaseSkill, SkillResult

class WebSearchSkill(BaseSkill):
    name = "web_search"
    description = "Recherche sur internet via DuckDuckGo"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "La requête de recherche"}
        },
        "required": ["query"]
    }

    async def run(self, query: str, **kwargs) -> SkillResult:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1}
            )
            data = r.json()
            results = [t["Text"] for t in data.get("RelatedTopics", [])[:5] if t.get("Text")]
            return SkillResult(success=True, output="\n".join(results))
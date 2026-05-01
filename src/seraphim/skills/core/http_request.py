from seraphim.skills.base import BaseSkill, SkillResult

_MAX_BODY = 4000


class HttpRequestSkill(BaseSkill):
    name = "http_request"
    description = (
        "Make HTTP requests (GET, POST, PUT, DELETE). "
        "Returns the status code and response body (truncated to 4000 chars)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Full URL to request",
            },
            "method": {
                "type": "string",
                "description": "HTTP method: GET, POST, PUT, DELETE (default: GET)",
                "default": "GET",
            },
            "headers": {
                "type": "object",
                "description": "Optional HTTP headers as key-value pairs",
            },
            "body": {
                "type": "string",
                "description": "Optional request body (for POST/PUT)",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default: 15)",
                "default": 15,
            },
        },
        "required": ["url"],
    }

    async def run(
        self,
        url: str,
        method: str = "GET",
        headers: dict = None,
        body: str = None,
        timeout: int = 15,
        **kwargs,
    ) -> SkillResult:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(
                    method=method.upper(),
                    url=url,
                    headers=headers or {},
                    content=body.encode() if body else None,
                )
            output = f"Status: {resp.status_code}\n\n{resp.text[:_MAX_BODY]}"
            return SkillResult(success=resp.status_code < 400, output=output)
        except Exception as e:
            return SkillResult(success=False, output="", error=str(e))

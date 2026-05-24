import ipaddress
import logging
import socket
from urllib.parse import urlparse

from seraphim.skills.base import BaseSkill, SkillResult

logger = logging.getLogger(__name__)

_MAX_BODY = 12000
_MAX_TIMEOUT = 60       # server-side cap regardless of what the LLM asks
_MAX_BODY_UPLOAD = 1024 * 1024  # 1 MB request body cap

# Hosts/prefixes that are never reachable from an AI assistant
_BLOCKED_HOSTS = frozenset(["localhost", "127.0.0.1", "::1", "0.0.0.0", "metadata.google.internal"])
_PRIVATE_PREFIXES = ("10.", "192.168.", "169.254.", "fc00:", "fd")
_PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / cloud metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _is_ssrf_url(url: str) -> bool:
    """Return True if the URL resolves to a private/loopback address."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if not host:
            return True
        if host.lower() in _BLOCKED_HOSTS:
            return True
        # Resolve hostname → IP (catches DNS rebinding)
        addr = socket.gethostbyname(host)
        ip = ipaddress.ip_address(addr)
        if ip.is_loopback or ip.is_link_local or ip.is_private:
            return True
        for net in _PRIVATE_RANGES:
            if ip in net:
                return True
        return False
    except (socket.gaierror, ValueError):
        return False  # can't resolve → let httpx handle it


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
                "description": f"Timeout in seconds (default: 15, max: {_MAX_TIMEOUT})",
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
        # Validate URL scheme
        if not url.lower().startswith(("http://", "https://")):
            return SkillResult(success=False, output="", error="URL must start with http:// or https://")

        # SSRF protection — block private/internal addresses
        if _is_ssrf_url(url):
            logger.warning("http_request blocked SSRF attempt: %s", url)
            return SkillResult(success=False, output="", error="URL resolves to a private/internal address and is not allowed")

        # Server-side caps
        timeout = min(int(timeout), _MAX_TIMEOUT)
        body_bytes = body.encode()[:_MAX_BODY_UPLOAD] if body else None

        # Only allow safe methods
        method = method.upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
            return SkillResult(success=False, output="", error=f"Unsupported HTTP method: {method}")

        try:
            import httpx
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.request(
                    method=method,
                    url=url,
                    headers=headers or {},
                    content=body_bytes,
                )
            output = f"Status: {resp.status_code}\n\n{resp.text[:_MAX_BODY]}"
            return SkillResult(success=resp.status_code < 400, output=output)
        except Exception as e:
            logger.debug("http_request failed for %s: %s", url, e)
            return SkillResult(success=False, output="", error=str(e))

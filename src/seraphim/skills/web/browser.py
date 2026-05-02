"""BrowserSkills — navigation et recherche via vrais navigateurs (Chrome, Edge, Firefox).

Utilise agent-browser (CDP) comme moteur. Chrome et Edge sont CDP-natifs.
Firefox supporté en mode limité.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote

from seraphim.skills.base import BaseSkill, SkillResult
from seraphim.skills.core.shell import ShellSkill

_BROWSERS: dict[str, str] = {
    "chrome":  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "edge":    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "firefox": r"C:\Program Files\Mozilla Firefox\firefox.exe",
}

# CDP-compatible (Chromium-based) — agent-browser fully supported
_CDP_BROWSERS = {"chrome", "edge"}

_SEARCH_URLS: dict[str, str] = {
    "duckduckgo": "https://duckduckgo.com/?q={query}",
    "google":     "https://www.google.com/search?q={query}",
    # setlang=en → English results for technical queries (better relevance)
    "bing":       "https://www.bing.com/news/search?q={query}&setlang=en&mkt=en-US",
}

# Preferred browser per engine (same ecosystem = less CAPTCHA/consent)
_ENGINE_BROWSER: dict[str, str] = {
    "bing":   "edge",
    "google": "chrome",
    "duckduckgo": "auto",
}

_MAX_SEARCH_OUTPUT = 4000


def _extract_search_text(raw: str) -> str:
    """Extract only the main results section from a snapshot, strip UI chrome."""
    lines = raw.splitlines()
    result_lines = []
    in_main = False

    for line in lines:
        # Skip agent-browser status lines
        if line.startswith(("✓ Closed", "No active sessions", "✓ Browser", "✓ Done", "✓ ")):
            continue
        # Start capturing at main results section
        if 'main "Search Results"' in line or 'main "Résultats de la recherche"' in line or 'main "News"' in line:
            in_main = True
        if in_main:
            # Keep heading, paragraph, link, StaticText lines — skip structural noise
            stripped = line.strip()
            if any(k in stripped for k in ('heading "', 'paragraph', 'StaticText "', 'link "', 'https://', 'http://')):
                result_lines.append(stripped)

    if not result_lines:
        # Fallback: return raw minus status lines
        fallback = "\n".join(
            l for l in lines
            if not l.startswith(("✓ Closed", "No active sessions", "✓ Browser", "✓ Done"))
        )
        return fallback.strip()[:_MAX_SEARCH_OUTPUT]

    return "\n".join(result_lines)[:_MAX_SEARCH_OUTPUT]


def _detect_browsers() -> dict[str, str]:
    """Return {name: exe_path} for each installed browser."""
    found: dict[str, str] = {}
    for name, path in _BROWSERS.items():
        if Path(path).exists():
            found[name] = path
    return found


def _best_cdp_browser() -> tuple[str, str] | None:
    """Return (name, exe_path) of best CDP-compatible browser, or None."""
    installed = _detect_browsers()
    for preferred in ("chrome", "edge"):
        if preferred in installed:
            return preferred, installed[preferred]
    return None


def _exe_flag(browser: str) -> str:
    """Build --executable-path flag for agent-browser, or '' for default."""
    installed = _detect_browsers()
    if browser == "auto":
        best = _best_cdp_browser()
        if best:
            _, path = best
            return f'--executable-path "{path}"'
        return ""
    if browser in installed:
        return f'--executable-path "{installed[browser]}"'
    return ""


class BrowserListSkill(BaseSkill):
    name = "browser_list"
    description = "List browsers installed on this machine (Chrome, Edge, Firefox)."
    parameters = {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs) -> SkillResult:
        installed = _detect_browsers()
        if not installed:
            return SkillResult(success=False, output="", error="No browsers found.")
        lines = []
        for name, path in installed.items():
            cdp = "(CDP/Chromium)" if name in _CDP_BROWSERS else "(Gecko — CDP limited)"
            lines.append(f"- {name}: {path} {cdp}")
        return SkillResult(success=True, output="\n".join(lines))


class BrowserNavigateSkill(BaseSkill):
    name = "browser_navigate"
    description = (
        "Open a URL in a real browser (Chrome/Edge/Firefox) and return page content. "
        "Actions: read (text), snapshot (accessibility tree), screenshot (PNG file). "
        "Use instead of http_request when the page needs JavaScript or login state."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Full URL to navigate to",
            },
            "action": {
                "type": "string",
                "description": "What to do: 'read' (default), 'snapshot', 'screenshot'",
                "default": "read",
            },
            "browser": {
                "type": "string",
                "description": "Browser to use: 'auto' (default), 'chrome', 'edge', 'firefox'",
                "default": "auto",
            },
            "output_file": {
                "type": "string",
                "description": "For screenshot action: output filename (default: page_screenshot.png)",
                "default": "page_screenshot.png",
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds (default: 60)",
                "default": 60,
            },
        },
        "required": ["url"],
    }

    async def run(
        self,
        url: str,
        action: str = "read",
        browser: str = "auto",
        output_file: str = "page_screenshot.png",
        timeout: int = 60,
        **kwargs,
    ) -> SkillResult:
        flag = _exe_flag(browser)
        shell = ShellSkill()
        # Always close first so --executable-path is respected
        close_first = "agent-browser close --all 2>nul & "

        if action == "screenshot":
            cmd = (
                close_first +
                f"agent-browser {flag} open \"{url}\" && "
                f"agent-browser wait --load networkidle && "
                f"agent-browser screenshot \"{output_file}\" && "
                f"agent-browser close --all"
            )
        elif action == "snapshot":
            cmd = (
                close_first +
                f"agent-browser {flag} open \"{url}\" && "
                f"agent-browser wait --load networkidle && "
                f"agent-browser snapshot -i && "
                f"agent-browser close --all"
            )
        else:  # read
            cmd = (
                close_first +
                f"agent-browser {flag} open \"{url}\" && "
                f"agent-browser wait --load networkidle && "
                f"agent-browser snapshot && "
                f"agent-browser close --all"
            )

        result = await shell.run(cmd, timeout=timeout)
        if not result.success and "No browsers" in result.error:
            return SkillResult(
                success=False, output="",
                error="No CDP-compatible browser found. Install Chrome or Edge."
            )
        return result


class BrowserSearchSkill(BaseSkill):
    name = "browser_search"
    description = (
        "Search the web using a real browser (Chrome/Edge). "
        "Handles JS-rendered pages and anti-bot protections better than plain HTTP. "
        "Use for searches where DuckDuckGo API fails or returns no results."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "engine": {
                "type": "string",
                "description": "Search engine: 'bing' (default), 'google', 'duckduckgo'",
                "default": "bing",
            },
            "browser": {
                "type": "string",
                "description": "Browser: 'auto' (default = best for engine), 'chrome', 'edge'",
                "default": "auto",
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds (default: 60)",
                "default": 60,
            },
        },
        "required": ["query"],
    }

    async def run(
        self,
        query: str,
        engine: str = "bing",
        browser: str = "auto",
        timeout: int = 90,
        **kwargs,
    ) -> SkillResult:
        template = _SEARCH_URLS.get(engine, _SEARCH_URLS["bing"])
        search_url = template.format(query=quote(query))

        # Use preferred browser for this engine if not overridden
        effective_browser = browser if browser != "auto" else _ENGINE_BROWSER.get(engine, "auto")
        flag = _exe_flag(effective_browser)
        shell = ShellSkill()
        close_first = "agent-browser close --all 2>nul & "

        # All engines need networkidle wait — results are JS-rendered
        cmd = (
            close_first +
            f"agent-browser {flag} open \"{search_url}\" && "
            f"agent-browser wait --load networkidle && "
            f"agent-browser snapshot && "
            f"agent-browser close --all"
        )
        result = await shell.run(cmd, timeout=timeout)
        if result.success:
            return SkillResult(
                success=True,
                output=_extract_search_text(result.output),
            )
        return result

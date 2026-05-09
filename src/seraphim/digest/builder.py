"""morning_digest builder — weather + news + monitor summary + LLM synthesis."""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path.home() / ".seraphim" / "digest.json"

_DEFAULT_CONFIG: dict[str, Any] = {
    "city": "Paris",
    "topics": ["tech", "AI", "crypto", "world news"],
    "news_per_topic": 3,
    "language": "fr",
    "save_dir": str(Path.home() / ".seraphim" / "digests"),
    "email_max": 10,
    "google_enabled": True,
}


def load_config() -> dict[str, Any]:
    if _CONFIG_PATH.exists():
        try:
            return {**_DEFAULT_CONFIG, **json.loads(_CONFIG_PATH.read_text("utf-8"))}
        except Exception:
            pass
    return _DEFAULT_CONFIG.copy()


def save_config(cfg: dict[str, Any]) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")


@dataclass
class DigestSection:
    title: str
    content: str
    error: str = ""


@dataclass
class Digest:
    date: str
    sections: list[DigestSection] = field(default_factory=list)
    summary: str = ""

    def to_markdown(self) -> str:
        lines = [f"# Morning Digest — {self.date}\n"]
        for s in self.sections:
            lines.append(f"## {s.title}")
            if s.error:
                lines.append(f"*Error: {s.error}*")
            else:
                lines.append(s.content)
            lines.append("")
        if self.summary:
            lines.append("## Summary")
            lines.append(self.summary)
        return "\n".join(lines)


async def _get_weather(city: str) -> DigestSection:
    url = f"https://wttr.in/{urllib.request.quote(city)}?format=j1"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())

        cur = data["current_condition"][0]
        desc = cur["weatherDesc"][0]["value"]
        temp_c = cur["temp_C"]
        feels = cur["FeelsLikeC"]
        humidity = cur["humidity"]
        wind = cur["windspeedKmph"]

        today = data["weather"][0]
        max_c = today["maxtempC"]
        min_c = today["mintempC"]

        content = (
            f"{desc}, {temp_c}°C (feels {feels}°C)\n"
            f"Min/Max: {min_c}°C / {max_c}°C\n"
            f"Humidity: {humidity}%  Wind: {wind} km/h"
        )
        return DigestSection(title=f"Weather — {city}", content=content)
    except Exception as e:
        return DigestSection(title=f"Weather — {city}", content="", error=str(e))


async def _get_news(topics: list[str], per_topic: int = 3) -> list[DigestSection]:
    sections: list[DigestSection] = []
    try:
        from ddgs import DDGS
        for topic in topics:
            try:
                query = f"{topic} news today"
                with DDGS() as ddgs:
                    results = list(ddgs.news(query, max_results=per_topic))
                if not results:
                    results = list(DDGS().text(query, max_results=per_topic))

                lines = []
                for r in results:
                    title = r.get("title") or r.get("title", "")
                    body = r.get("body") or r.get("excerpt", "")
                    url = r.get("url") or r.get("href", "")
                    lines.append(f"• **{title}**")
                    if body:
                        lines.append(f"  {body[:120]}")
                    if url:
                        lines.append(f"  {url}")
                sections.append(DigestSection(title=f"News — {topic.title()}", content="\n".join(lines)))
            except Exception as e:
                sections.append(DigestSection(title=f"News — {topic.title()}", content="", error=str(e)))
    except ImportError:
        sections.append(DigestSection(title="News", content="", error="ddgs not installed"))
    return sections


async def _get_emails(max_results: int = 10) -> DigestSection:
    try:
        from seraphim.connectors.gmail import gmail_connector
        if not gmail_connector.is_connected():
            return DigestSection(title="Emails", content="", error="Not connected — run: seraphim digest auth")
        emails = gmail_connector.get_today_emails(max_results=max_results)
        unread = gmail_connector.get_unread_count()
        if not emails:
            return DigestSection(title="Emails", content="No emails today.")
        lines = [f"**{unread} unread** today\n"]
        for e in emails:
            lines.append(f"• **{e['subject']}**")
            lines.append(f"  From: {e['from']}")
            if e["snippet"]:
                lines.append(f"  {e['snippet'][:100]}")
        return DigestSection(title="Emails", content="\n".join(lines))
    except Exception as e:
        return DigestSection(title="Emails", content="", error=str(e))


async def _get_calendar() -> DigestSection:
    try:
        from seraphim.connectors.gcalendar import gcalendar_connector
        if not gcalendar_connector.is_connected():
            return DigestSection(title="Calendar", content="", error="Not connected — run: seraphim digest auth")
        events = gcalendar_connector.get_today_events()
        if not events:
            return DigestSection(title="Calendar", content="No events today.")
        lines = []
        for e in events:
            time_range = f"{e['start']}–{e['end']}" if e["start"] else "all day"
            line = f"• **{e['title']}** — {time_range}"
            if e["location"]:
                line += f" @ {e['location']}"
            lines.append(line)
            if e["attendees"]:
                lines.append(f"  With: {', '.join(e['attendees'][:3])}")
        return DigestSection(title="Calendar", content="\n".join(lines))
    except Exception as e:
        return DigestSection(title="Calendar", content="", error=str(e))


async def _get_monitor_summary() -> DigestSection:
    try:
        from seraphim.monitor.store import init_db, list_monitors
        await init_db()
        monitors = await list_monitors()
        if not monitors:
            return DigestSection(title="Monitors", content="No monitors configured.")

        lines = []
        for m in monitors:
            status = "on" if m["enabled"] else "off"
            last = "never"
            if m["last_check"]:
                delta = int(time.time() - m["last_check"])
                last = f"{delta // 60}m ago" if delta < 3600 else f"{delta // 3600}h ago"
            triggered = m["triggered_count"]
            lines.append(
                f"• **{m['name']}** [{status}] — checked {last}, triggered {triggered}x"
            )
            if m["last_result"] and triggered > 0:
                lines.append(f"  Last: {m['last_result'][:80]}")

        return DigestSection(title="Monitors", content="\n".join(lines))
    except Exception as e:
        return DigestSection(title="Monitors", content="", error=str(e))


async def _llm_summary(digest: Digest, language: str = "fr") -> str:
    try:
        from seraphim.engine import get_engine

        lang = "français" if language == "fr" else "English"

        # Build compact bullet summary — skip emails (too noisy), cap each section
        bullets: list[str] = []
        for s in digest.sections:
            if s.error or not s.content or s.title.startswith("Email"):
                continue
            first_line = s.content.splitlines()[0][:120]
            bullets.append(f"- {s.title}: {first_line}")

        if not bullets:
            return ""

        data = "\n".join(bullets)

        messages = [
            {
                "role": "system",
                "content": (
                    f"Tu es un assistant briefing matinal. Réponds uniquement en {lang}. "
                    "Tu dois écrire UN court paragraphe de 3 à 5 phrases maximum résumant les points clés. "
                    "N'inclus PAS les données brutes. N'utilise PAS de listes. Synthèse uniquement."
                ),
            },
            {
                "role": "user",
                "content": f"Voici les données du digest:\n{data}\n\nÉcris le résumé maintenant.",
            },
        ]

        engine = get_engine()
        result = await engine.chat(messages, max_tokens=300, temperature=0.5)
        text = result["messages"][0].get("content", "").strip()
        return text
    except Exception:
        return ""


async def build_digest(cfg: dict[str, Any] | None = None) -> Digest:
    if cfg is None:
        cfg = load_config()

    date_str = datetime.now().strftime("%A %d %B %Y — %H:%M")
    digest = Digest(date=date_str)

    # Weather
    weather = await _get_weather(cfg["city"])
    digest.sections.append(weather)

    # Calendar (before news — time-critical)
    if cfg.get("google_enabled", True):
        calendar_section = await _get_calendar()
        digest.sections.append(calendar_section)

    # Emails
    if cfg.get("google_enabled", True):
        email_section = await _get_emails(max_results=cfg.get("email_max", 10))
        digest.sections.append(email_section)

    # News per topic
    news_sections = await _get_news(cfg["topics"], cfg["news_per_topic"])
    digest.sections.extend(news_sections)

    # Monitor status
    monitor_section = await _get_monitor_summary()
    digest.sections.append(monitor_section)

    # LLM synthesis
    if not cfg.get("_skip_summary"):
        digest.summary = await _llm_summary(digest, cfg.get("language", "fr"))

    return digest

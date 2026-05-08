"""Google Calendar connector — fetch today's events via Calendar REST API v3."""

from __future__ import annotations

import json
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Optional

_BASE = "https://www.googleapis.com/calendar/v3"


def _api_get(path: str, access_token: str) -> dict:
    req = urllib.request.Request(
        f"{_BASE}/{path}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _list_events(calendar_id: str, time_min: str, time_max: str, access_token: str) -> list[dict]:
    params = "&".join([
        f"timeMin={urllib.request.quote(time_min)}",
        f"timeMax={urllib.request.quote(time_max)}",
        "singleEvents=true",
        "orderBy=startTime",
        "maxResults=20",
    ])
    cid = urllib.request.quote(calendar_id, safe="")
    data = _api_get(f"calendars/{cid}/events?{params}", access_token)
    return data.get("items", [])


def _format_event(event: dict) -> dict:
    start = event.get("start", {})
    end = event.get("end", {})
    start_str = start.get("dateTime", start.get("date", ""))
    end_str = end.get("dateTime", end.get("date", ""))

    def _fmt(dt_str: str) -> str:
        if not dt_str:
            return ""
        try:
            if "T" in dt_str:
                dt = datetime.fromisoformat(dt_str)
                return dt.strftime("%H:%M")
            return dt_str
        except Exception:
            return dt_str

    attendees = [
        a.get("displayName") or a.get("email", "")
        for a in event.get("attendees", [])
        if not a.get("self")
    ]

    return {
        "title": event.get("summary", "(no title)"),
        "start": _fmt(start_str),
        "end": _fmt(end_str),
        "location": event.get("location", ""),
        "organizer": event.get("organizer", {}).get("displayName", ""),
        "attendees": attendees,
        "description": (event.get("description") or "")[:200],
        "all_day": "date" in start and "dateTime" not in start,
    }


class GCalendarConnector:
    def is_connected(self) -> bool:
        from seraphim.connectors.oauth import is_connected
        return is_connected()

    def get_today_events(self) -> list[dict]:
        from seraphim.connectors.oauth import get_access_token
        token = get_access_token()

        today = date.today()
        time_min = datetime(today.year, today.month, today.day, tzinfo=timezone.utc).isoformat()
        time_max = (datetime(today.year, today.month, today.day, tzinfo=timezone.utc) + timedelta(days=1)).isoformat()

        raw_events = _list_events("primary", time_min, time_max, token)
        return [_format_event(e) for e in raw_events]

    def get_next_event(self) -> Optional[dict]:
        events = self.get_today_events()
        now_str = datetime.now().strftime("%H:%M")
        for e in events:
            if e["start"] and e["start"] >= now_str:
                return e
        return None


gcalendar_connector = GCalendarConnector()

"""Gmail connector — fetch today's emails via Gmail REST API v1."""

from __future__ import annotations

import base64
import json
import urllib.request
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def _api_get(path: str, access_token: str) -> dict:
    req = urllib.request.Request(
        f"{_BASE}/{path}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _api_list_messages(query: str, max_results: int, access_token: str) -> list[dict]:
    params = f"q={urllib.request.quote(query)}&maxResults={max_results}"
    data = _api_get(f"messages?{params}", access_token)
    return data.get("messages", [])


def _extract_header(headers: list[dict], name: str) -> str:
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value", "")
    return ""


def _decode_body(payload: dict) -> str:
    if "body" in payload and payload["body"].get("data"):
        raw = payload["body"]["data"]
        return base64.urlsafe_b64decode(raw + "==").decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain":
            raw = part.get("body", {}).get("data", "")
            if raw:
                return base64.urlsafe_b64decode(raw + "==").decode("utf-8", errors="replace")
    return ""


def _parse_date(date_str: str) -> Optional[datetime]:
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        return None


class GmailConnector:
    def is_connected(self) -> bool:
        from seraphim.connectors.oauth import is_connected
        return is_connected()

    def get_today_emails(self, max_results: int = 10) -> list[dict]:
        from seraphim.connectors.oauth import get_access_token
        token = get_access_token()
        today = date.today().strftime("%Y/%m/%d")
        messages = _api_list_messages(f"after:{today}", max_results, token)

        emails = []
        for msg_ref in messages:
            try:
                msg = _api_get(f"messages/{msg_ref['id']}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date", token)
                headers = msg.get("payload", {}).get("headers", [])
                emails.append({
                    "subject": _extract_header(headers, "Subject") or "(no subject)",
                    "from": _extract_header(headers, "From"),
                    "date": _extract_header(headers, "Date"),
                    "snippet": msg.get("snippet", ""),
                })
            except Exception:
                continue
        return emails

    def get_unread_count(self) -> int:
        from seraphim.connectors.oauth import get_access_token
        token = get_access_token()
        today = date.today().strftime("%Y/%m/%d")
        msgs = _api_list_messages(f"is:unread after:{today}", 50, token)
        return len(msgs)


gmail_connector = GmailConnector()

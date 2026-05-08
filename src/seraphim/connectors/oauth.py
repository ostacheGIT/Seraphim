"""Google OAuth 2.0 helper — browser flow + token persistence.

Setup:
  1. Go to https://console.cloud.google.com/ → APIs & Services → Credentials
  2. Create OAuth 2.0 Client ID (Desktop app)
  3. Enable Gmail API and Google Calendar API
  4. Run: seraphim digest auth --client-id <id> --client-secret <secret>
"""

from __future__ import annotations

import http.server
import json
import os
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

_BASE_DIR = Path.home() / ".seraphim" / "connectors"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CALLBACK_PORT = 8765
_CALLBACK_PATH = "/oauth/callback"


def _creds_path() -> Path:
    return _BASE_DIR / "google_client.json"


def _token_path() -> Path:
    return _BASE_DIR / "google_token.json"


def save_client_credentials(client_id: str, client_secret: str) -> None:
    _BASE_DIR.mkdir(parents=True, exist_ok=True)
    path = _creds_path()
    path.write_text(json.dumps({"client_id": client_id, "client_secret": client_secret}))
    path.chmod(0o600)


def get_client_credentials() -> tuple[str, str]:
    path = _creds_path()
    if path.exists():
        data = json.loads(path.read_text())
        return data["client_id"], data["client_secret"]
    client_id = os.environ.get("SERAPHIM_GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("SERAPHIM_GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Google credentials not found. Run:\n"
            "  seraphim digest auth --client-id <id> --client-secret <secret>"
        )
    return client_id, client_secret


def load_tokens() -> Optional[dict]:
    path = _token_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    return None


def save_tokens(tokens: dict) -> None:
    _BASE_DIR.mkdir(parents=True, exist_ok=True)
    path = _token_path()
    path.write_text(json.dumps(tokens))
    path.chmod(0o600)


def delete_tokens() -> None:
    path = _token_path()
    if path.exists():
        path.unlink()


def is_connected() -> bool:
    tokens = load_tokens()
    return tokens is not None and bool(tokens.get("refresh_token"))


def _refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(_TOKEN_URL, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read())
    return resp


def get_access_token() -> str:
    tokens = load_tokens()
    if not tokens:
        raise RuntimeError("Not authenticated. Run: seraphim digest auth")

    expiry = tokens.get("expiry", 0)
    if time.time() < expiry - 60:
        return tokens["access_token"]

    client_id, client_secret = get_client_credentials()
    refreshed = _refresh_access_token(client_id, client_secret, tokens["refresh_token"])
    tokens["access_token"] = refreshed["access_token"]
    tokens["expiry"] = time.time() + refreshed.get("expires_in", 3600)
    save_tokens(tokens)
    return tokens["access_token"]


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    code: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            _CallbackHandler.code = params["code"][0]
            msg = b"<html><body><h2>Seraphim: authentication successful!</h2><p>You can close this tab.</p></body></html>"
        else:
            _CallbackHandler.error = params.get("error", ["unknown"])[0]
            msg = b"<html><body><h2>Seraphim: authentication failed.</h2></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(msg)

    def log_message(self, *args):
        pass


def run_oauth_flow() -> None:
    """Open browser, wait for callback, exchange code, save tokens."""
    client_id, client_secret = get_client_credentials()
    redirect_uri = f"http://localhost:{_CALLBACK_PORT}{_CALLBACK_PATH}"

    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    })
    auth_url = f"{_AUTH_URL}?{params}"

    _CallbackHandler.code = None
    _CallbackHandler.error = None

    server = http.server.HTTPServer(("localhost", _CALLBACK_PORT), _CallbackHandler)
    server.timeout = 1

    print(f"Opening browser for Google authentication...")
    print(f"If browser doesn't open, visit:\n  {auth_url}")
    webbrowser.open(auth_url)

    deadline = time.time() + 120
    while time.time() < deadline:
        server.handle_request()
        if _CallbackHandler.code or _CallbackHandler.error:
            break

    server.server_close()

    if _CallbackHandler.error:
        raise RuntimeError(f"OAuth error: {_CallbackHandler.error}")
    if not _CallbackHandler.code:
        raise RuntimeError("OAuth timeout — no code received within 2 minutes.")

    data = urllib.parse.urlencode({
        "code": _CallbackHandler.code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(_TOKEN_URL, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read())

    if "error" in resp:
        raise RuntimeError(f"Token exchange failed: {resp['error']}: {resp.get('error_description', '')}")

    tokens = {
        "access_token": resp["access_token"],
        "refresh_token": resp.get("refresh_token", ""),
        "expiry": time.time() + resp.get("expires_in", 3600),
    }
    save_tokens(tokens)
    print("Google authentication successful. Tokens saved.")

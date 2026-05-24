"""AppManagerSkill — ouvrir, fermer, focus, lister et interagir avec les apps Windows.

Actions:
  open         — lance une application par son nom commun
  close        — ferme gracieusement (CloseMainWindow → Stop-Process si besoin)
  status       — vérifie si une app tourne, retourne PID + RAM
  list_running — liste les apps GUI actives (fenêtres visibles uniquement)
  focus        — met une app au premier plan
  interact     — ouvre (si besoin) + met au premier plan + interagit :
                   text= taper du texte (clipboard-paste, aucun échappement)
                   url=  naviguer vers une URL (Ctrl+L → coller → Entrée)
                   keys= raccourci clavier (notation pywinauto : ^t, %{F4}…)
                   send_enter= True pour envoyer Entrée après le texte
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from seraphim.skills.base import BaseSkill, SkillResult


_PS_ENCODING_PREAMBLE = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "$OutputEncoding = [System.Text.Encoding]::UTF8\n"
)


def _ps(script: str, timeout: int = 10) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             _PS_ENCODING_PREAMBLE + script],
            capture_output=True, timeout=timeout,
        )
        out = (r.stdout or b"").decode("utf-8", errors="replace")
        err = (r.stderr or b"").decode("utf-8", errors="replace")
        return r.returncode == 0, (out or err).strip()
    except subprocess.TimeoutExpired:
        return False, f"Timeout after {timeout}s"
    except Exception as e:
        return False, str(e)


# ── App name → executable ─────────────────────────────────────────────────────

_APP_MAP: dict[str, str] = {
    # Navigateurs
    "chrome":           "chrome",
    "google chrome":    "chrome",
    "firefox":          "firefox",
    "edge":             "msedge",
    "microsoft edge":   "msedge",
    "brave":            "brave",
    "opera":            "opera",
    "vivaldi":          "vivaldi",
    # Communication
    "discord":          "discord",
    "slack":            "slack",
    "teams":            "ms-teams",
    "microsoft teams":  "ms-teams",
    "zoom":             "zoom",
    "telegram":         "telegram",
    "signal":           "signal",
    "whatsapp":         "whatsapp",
    "skype":            "skype",
    # Dev & terminal
    "vscode":           "code",
    "vs code":          "code",
    "visual studio code": "code",
    "code":             "code",
    "terminal":         "wt",
    "windows terminal": "wt",
    "powershell":       "pwsh",
    "cmd":              "cmd",
    "notepad++":        "notepad++",
    "sublime":          "subl",
    "sublime text":     "subl",
    "pycharm":          "pycharm64",
    "intellij":         "idea64",
    "git bash":         "git-bash",
    "postman":          "postman",
    # Office & productivité
    "word":             "winword",
    "excel":            "excel",
    "powerpoint":       "powerpnt",
    "onenote":          "onenote",
    "outlook":          "outlook",
    "libreoffice":      "soffice",
    "obsidian":         "obsidian",
    "notion":           "notion",
    # Médias
    "spotify":          "spotify",
    "vlc":              "vlc",
    "obs":              "obs64",
    "obs studio":       "obs64",
    "audacity":         "audacity",
    "foobar":           "foobar2000",
    "winamp":           "winamp",
    "media player":     "wmplayer",
    # Création
    "photoshop":        "photoshop",
    "gimp":             "gimp-2.10",
    "blender":          "blender",
    "premiere":         "premiere",
    "after effects":    "afterfx",
    "illustrator":      "illustrator",
    "figma":            "figma",
    "davinci":          "resolve",
    "davinci resolve":  "resolve",
    # Jeux & launchers
    "steam":            "steam",
    "epic":             "epicgameslauncher",
    "epic games":       "epicgameslauncher",
    "battle.net":       "battle.net",
    "battlenet":        "battle.net",
    "origin":           "origin",
    "gog":              "gogalaxy",
    "ubisoft":          "ubisoftconnect",
    "uplay":            "ubisoftconnect",
    # Système
    "explorer":         "explorer",
    "explorateur":      "explorer",
    "notepad":          "notepad",
    "bloc-notes":       "notepad",
    "calculator":       "calc",
    "calculatrice":     "calc",
    "paint":            "mspaint",
    "taskmgr":          "taskmgr",
    "gestionnaire":     "taskmgr",
    "regedit":          "regedit",
    "snipping":         "snippingtool",
    "capture":          "snippingtool",
    # Utilitaires
    "7zip":             "7zFM",
    "winrar":           "winrar",
    "everything":       "everything",
    "winscp":           "winscp",
    "putty":            "putty",
    "filezilla":        "filezilla",
    "bitwarden":        "bitwarden",
    "keepass":          "keepass",
    "nordvpn":          "nordvpn",
}

# Profils d'interaction par app (exe sans .exe)
_APP_PROFILES: dict[str, dict] = {
    # Navigateurs — Ctrl+L pour la barre d'adresse
    "chrome":     {"browser": True, "url_key": "^l", "backend": "uia"},
    "msedge":     {"browser": True, "url_key": "^l", "backend": "uia"},
    "firefox":    {"browser": True, "url_key": "^l", "backend": "uia"},
    "brave":      {"browser": True, "url_key": "^l", "backend": "uia"},
    "opera":      {"browser": True, "url_key": "^l", "backend": "uia"},
    "vivaldi":    {"browser": True, "url_key": "^l", "backend": "uia"},
    # Terminaux — Enter après la commande
    "wt":         {"terminal": True, "send_enter": True, "backend": "uia"},
    "cmd":        {"terminal": True, "send_enter": True, "backend": "win32"},
    "pwsh":       {"terminal": True, "send_enter": True, "backend": "uia"},
    # Éditeurs de texte — type direct
    "notepad":    {"editor": True, "backend": "win32"},
    "notepad++":  {"editor": True, "backend": "uia"},
    "code":       {"editor": True, "backend": "uia",
                   "quick_open": "^p", "command_palette": "^+p", "terminal": "^`"},
    "subl":       {"editor": True, "backend": "uia"},
    # Apps spéciales
    "spotify":    {"backend": "uia"},
    "discord":    {"backend": "uia"},
    "obsidian":   {"editor": True, "backend": "uia"},
    "explorer":   {"backend": "uia"},
}


def _resolve_app(name: str) -> str | None:
    key = name.lower().strip()
    if key in _APP_MAP:
        return _APP_MAP[key]
    try:
        from rapidfuzz import process as rfp, fuzz
        match, score, _ = rfp.extractOne(key, _APP_MAP.keys(), scorer=fuzz.WRatio)
        if score >= 72:
            return _APP_MAP[match]
    except ImportError:
        for k, v in _APP_MAP.items():
            if key in k or k in key:
                return v
    return None


def _proc_name(exe: str) -> str:
    return exe.replace(".exe", "").replace(".EXE", "")


# ── pywinauto helpers (sync, run in thread) ───────────────────────────────────

# Mots-clés de titre de fenêtre pour les apps UWP / multi-process
_APP_TITLE_HINTS: dict[str, list[str]] = {
    "notepad":      ["notepad", "bloc-notes", "sans titre", "untitled"],
    "calculator":   ["calculator", "calculatrice"],
    "snippingtool": ["snipping", "capture", "outil"],
    "mspaint":      ["paint"],
    "winword":      ["word", ".doc"],
    "excel":        ["excel", ".xls"],
    "powerpnt":     ["powerpoint", ".ppt"],
    "outlook":      ["outlook"],
    "onenote":      ["onenote"],
}


def _find_pywinauto_window(app: str, pid: int | None = None):
    """Find a pywinauto window for the given app.

    Tries PID first (reliable for Win32), then falls back to title-based
    search via Desktop enumeration (needed for UWP / ApplicationFrameHost).
    Returns (win, backend) or (None, None).
    """
    from pywinauto import Desktop, Application

    exe = _resolve_app(app) or app
    safe_exe = _proc_name(exe)
    backend = _APP_PROFILES.get(safe_exe, {}).get("backend", "uia")

    # 1. PID-based (non-UWP apps)
    if pid:
        try:
            pw_app = Application(backend=backend).connect(process=pid, timeout=3)
            return pw_app.top_window(), backend
        except Exception:
            pass

    # 2. Title-based fallback (UWP, multi-process like Chrome/Discord)
    title_hints = _APP_TITLE_HINTS.get(safe_exe.lower(), [safe_exe.lower(), app.lower()])
    try:
        for w in Desktop(backend=backend).windows():
            title = (w.window_text() or "").lower()
            if any(h in title for h in title_hints):
                return w, backend
    except Exception:
        pass

    return None, None


def _pw_get_window(pid: int, backend: str = "uia"):
    """Return the main pywinauto window for a PID."""
    from pywinauto import Application
    app = Application(backend=backend).connect(process=pid, timeout=5)
    return app.top_window()


def _pw_interact_win(
    win,  # pywinauto WindowSpecification
    backend: str,
    text: str, url: str, keys: str,
    element_hint: str, send_enter: bool,
) -> tuple[bool, str]:
    """Core pywinauto interaction on an already-focused window."""
    import time
    try:
        if url:
            win.type_keys("^l", pause=0.05)
            time.sleep(0.3)
            win.type_keys("^a", pause=0.05)
            _set_clipboard(url)
            win.type_keys("^v", pause=0.05)
            time.sleep(0.1)
            win.type_keys("{ENTER}", pause=0.05)
            return True, f"✓ Navigation vers {url}"

        if text:
            target = win
            if element_hint:
                try:
                    el = win.child_window(title_re=element_hint, found_index=0)
                    el.set_focus()
                    time.sleep(0.1)
                    target = el
                except Exception:
                    pass
            _set_clipboard(text)
            target.type_keys("^v", pause=0.05)
            if send_enter:
                time.sleep(0.05)
                target.type_keys("{ENTER}", pause=0.05)
            return True, f"✓ Texte tapé ({len(text)} car.)"

        if keys:
            win.type_keys(keys, pause=0.05)
            return True, f"✓ Raccourci {keys} envoyé."

        return True, "✓ Fenêtre mise au premier plan."
    except Exception as e:
        return False, str(e)


def _pw_interact_sync(
    pid: int, backend: str,
    text: str, url: str, keys: str,
    element_hint: str, send_enter: bool,
) -> tuple[bool, str]:
    """Synchronous pywinauto interaction via PID — called via asyncio.to_thread."""
    import time
    try:
        from pywinauto import Application
        app = Application(backend=backend).connect(process=pid, timeout=5)
        win = app.top_window()
        win.set_focus()
        time.sleep(0.25)
        return _pw_interact_win(win, backend, text, url, keys, element_hint, send_enter)
    except Exception as e:
        return False, str(e)


def _set_clipboard(text: str) -> None:
    """Write text to Windows clipboard via PowerShell (no pyperclip dep)."""
    safe = text.replace("'", "''")
    _ps(f"Set-Clipboard -Value '{safe}'", timeout=3)


# ── SendKeys fallback via PowerShell ─────────────────────────────────────────

def _sendkeys_interact(text: str, url: str, keys: str, send_enter: bool) -> tuple[bool, str]:
    parts = [
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
        "Add-Type -AssemblyName System.Windows.Forms",
        "Start-Sleep -Milliseconds 200",
    ]
    if url:
        safe = url.replace("'", "''")
        parts += [
            "[System.Windows.Forms.SendKeys]::SendWait('^l')",
            "Start-Sleep -Milliseconds 300",
            f"Set-Clipboard -Value '{safe}'",
            "[System.Windows.Forms.SendKeys]::SendWait('^v')",
            "Start-Sleep -Milliseconds 100",
            "[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')",
        ]
        label = f"✓ Navigation vers {url}"
    elif text:
        safe = text.replace("'", "''")
        parts += [
            f"Set-Clipboard -Value '{safe}'",
            "Start-Sleep -Milliseconds 100",
            "[System.Windows.Forms.SendKeys]::SendWait('^v')",
        ]
        if send_enter:
            parts.append("[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')")
        label = f"✓ Texte tapé ({len(text)} car.)"
    elif keys:
        safe_k = keys.replace("'", "''")
        parts.append(f"[System.Windows.Forms.SendKeys]::SendWait('{safe_k}')")
        label = f"✓ Raccourci {keys} envoyé."
    else:
        return True, "✓ Focus mis à jour."

    ok, out = _ps("\n".join(parts), timeout=8)
    return ok, label if ok else out


# ── Compound query parser ─────────────────────────────────────────────────────

# Patterns reconnus dans la partie "et [action]"
_URL_RE = re.compile(
    r"(?:va\s+sur|navigue(?:\s+vers)?|ouvre(?:\s+le\s+site)?|visite|go\s+to|open|navigate\s+to)\s+"
    r"((?:https?://)?[\w\-]+(?:\.[\w\-]+)+(?:/\S*)?)",
    re.I,
)
# "va sur youtube" (sans TLD) → youtube.com  — at least 4 chars, alone at end of string
_NAV_BARE_RE = re.compile(
    r"(?:va\s+sur|navigue(?:\s+vers)?|ouvre(?:\s+le\s+site)?|visite|go\s+to|navigate\s+to)\s+"
    r"([a-z][\w\-]{3,})\s*$",
    re.I,
)
_NAV_BARE_SKIP = frozenset({"nous", "eux", "elle", "elles", "lui", "moi", "toi", "vous", "cela", "rien"})
# Navigation intent guard — prevents typing "va sur youtube" as plain text if URL parsing failed
_NAV_INTENT_RE = re.compile(
    r"(?:va\s+sur|navigue(?:\s+vers)?|visite|go\s+to|navigate\s+to)\s+",
    re.I,
)
_TYPE_RE = re.compile(
    r"(?:écri[st]|tape[rz]?|entre[rz]?|saisit?|write|type)\s+['\"]?(.+?)['\"]?\s*$",
    re.I,
)
_SEARCH_RE = re.compile(
    r"(?:cherche[rz]?|recherche[rz]?|google[rz]?|search(?:\s+for)?)\s+(.+?)\s*$",
    re.I,
)
_CMD_RE = re.compile(
    r"(?:lance[rz]?|exécute[rz]?|run[sz]?|tape[rz]?)\s+(?:la\s+commande\s+)?['\"`]?(.+?)['\"`]?\s*$",
    re.I,
)

# Raccourcis nommés → notation pywinauto
_NAMED_KEYS: dict[str, str] = {
    "entrée": "{ENTER}", "enter": "{ENTER}",
    "tab": "{TAB}", "tabulation": "{TAB}",
    "échap": "{ESC}", "escape": "{ESC}", "esc": "{ESC}",
    "suppr": "{DELETE}", "delete": "{DELETE}",
    "espace": "{SPACE}", "space": "{SPACE}",
    "ctrl+t": "^t", "ctrl+n": "^n", "ctrl+w": "^w",
    "ctrl+z": "^z", "ctrl+y": "^y",
    "ctrl+c": "^c", "ctrl+v": "^v", "ctrl+x": "^x",
    "ctrl+a": "^a", "ctrl+f": "^f", "ctrl+s": "^s",
    "ctrl+p": "^p", "ctrl+l": "^l",
    "alt+f4": "%{F4}",
}


# Articles à retirer du nom d'app (fr/en)
_ARTICLE_RE = re.compile(
    r"^(?:le\s+|la\s+|les\s+|l['\s]+|un\s+|une\s+|the\s+|a\s+|an\s+)",
    re.I,
)


def parse_interaction(do_text: str, profile: dict) -> dict:
    """Parse la partie 'et [action]' d'une commande composée.

    Returns dict with keys: text, url, keys, send_enter.
    Priority: URL > search > named_key > terminal_cmd > type_text > fallback.
    """
    result: dict = {}

    # 1. URL navigation (with explicit TLD or protocol)
    m = _URL_RE.search(do_text)
    if m:
        url = m.group(1).rstrip(".")  # strip trailing dot artefact
        if not url.startswith("http"):
            url = "https://" + url
        result["url"] = url
        return result

    # 1b. Navigation verb + bare site name without TLD: "va sur youtube" → https://youtube.com
    m = _NAV_BARE_RE.search(do_text)
    if m and m.group(1).lower() not in _NAV_BARE_SKIP:
        result["url"] = f"https://{m.group(1).lower()}.com"
        return result

    # 2. Recherche web → Google
    m = _SEARCH_RE.search(do_text)
    if m:
        import urllib.parse
        q = urllib.parse.quote_plus(m.group(1).strip())
        result["url"] = f"https://www.google.com/search?q={q}"
        return result

    # 3. Raccourci nommé (avant type pour "tape Ctrl+T", "appuie sur Enter"…)
    do_lower = do_text.lower().strip()
    for alias, notation in _NAMED_KEYS.items():
        if alias in do_lower:
            result["keys"] = notation
            return result

    # 4. Commande terminal (plus spécifique que type car force send_enter)
    m = _CMD_RE.search(do_text)
    if m:
        result["text"] = m.group(1).strip().strip("'\"`")
        result["send_enter"] = True
        return result

    # 5. Frappe texte libre
    m = _TYPE_RE.search(do_text)
    if m:
        result["text"] = m.group(1).strip().strip("'\"`")
        if profile.get("terminal"):
            result["send_enter"] = True
        return result

    # 6. Fallback: traiter tout comme texte à taper
    # Guard: don't type navigation intents that failed to parse as a URL
    if _NAV_INTENT_RE.search(do_text):
        return result  # empty → _interact will just focus the window
    text = do_text.strip().strip("'\"`")
    if text and len(text) < 500:
        result["text"] = text
        if profile.get("terminal"):
            result["send_enter"] = True
    return result


class AppManagerSkill(BaseSkill):
    name = "app_manager"
    description = (
        "Gère et interagit avec les applications Windows. "
        "Utilise pour :\n"
        "• Ouvrir  : 'ouvre Spotify', 'lance Chrome', 'démarre le terminal'\n"
        "• Fermer  : 'ferme Chrome', 'quitte Discord', 'arrête Zoom'\n"
        "• Status  : 'est-ce que Discord tourne ?', 'Spotify est actif ?'\n"
        "• Fenêtres: 'quelles apps sont ouvertes ?', 'apps actives'\n"
        "• Focus   : 'mets VS Code au premier plan', 'focus Discord'\n"
        "• Interagir: 'ouvre Chrome et va sur youtube.com', "
        "'ouvre Notepad et écris Bonjour', 'ouvre le terminal et tape git status'"
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["open", "close", "status", "list_running", "focus", "interact"],
                "description": (
                    "open: lancer | close: fermer | status: vérifier | "
                    "list_running: apps GUI ouvertes | focus: premier plan | "
                    "interact: ouvrir + interagir (text/url/keys)"
                ),
            },
            "app": {
                "type": "string",
                "description": "Nom de l'app (ex: chrome, discord, vscode). Requis sauf list_running.",
                "default": "",
            },
            "text": {
                "type": "string",
                "description": "[interact] Texte à taper dans l'app (clipboard-paste, tous caractères).",
                "default": "",
            },
            "url": {
                "type": "string",
                "description": "[interact] URL à ouvrir dans un navigateur (Ctrl+L → coller → Entrée).",
                "default": "",
            },
            "keys": {
                "type": "string",
                "description": "[interact] Raccourci clavier pywinauto (^t=Ctrl+T, %{F4}=Alt+F4, {ENTER}).",
                "default": "",
            },
            "send_enter": {
                "type": "boolean",
                "description": "[interact] Envoyer Entrée après le texte (utile pour terminal).",
                "default": False,
            },
            "element": {
                "type": "string",
                "description": "[interact] Nom/regex de l'élément UI cible (optionnel, pywinauto).",
                "default": "",
            },
            "force": {
                "type": "boolean",
                "description": "[close] Forcer l'arrêt immédiat sans attente.",
                "default": False,
            },
        },
        "required": ["action"],
    }

    # ── open ──────────────────────────────────────────────────────────────────

    async def _open(self, app: str) -> SkillResult:
        if not app:
            return SkillResult(success=False, output="", error="Paramètre 'app' requis.")
        exe = _resolve_app(app) or app
        safe_exe = exe.replace("'", "").replace(";", "")
        # Try direct Start-Process; fall back to App Paths registry (browsers, etc.
        # are not in PATH but are registered under
        # HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\<exe>.exe)
        script = f"""
$exe = '{safe_exe}'
$launched = $false
try {{
    Start-Process $exe -ErrorAction Stop
    $launched = $true
}} catch {{}}
if (-not $launched) {{
    $reg = 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\' + $exe + '.exe'
    $fp = (Get-ItemProperty $reg -ErrorAction SilentlyContinue).'(default)'
    if ($fp -and (Test-Path $fp)) {{
        Start-Process $fp -ErrorAction SilentlyContinue
        $launched = $true
    }}
}}
if ($launched) {{ 'launched' }} else {{ 'failed' }}
"""
        ok, out = _ps(script, timeout=8)
        if "launched" in out:
            return SkillResult(success=True, output=f"✓ {app.title()} lancé.")
        return SkillResult(success=False, output="", error=f"Impossible de lancer '{app}' : {out}")

    # ── close ─────────────────────────────────────────────────────────────────

    async def _close(self, app: str, force: bool) -> SkillResult:
        if not app:
            return SkillResult(success=False, output="", error="Paramètre 'app' requis.")
        exe = _resolve_app(app) or app
        safe = _proc_name(exe).replace("'", "").replace(";", "")
        if force:
            ok, out = _ps(f"Stop-Process -Name '{safe}' -Force -ErrorAction Stop")
            return SkillResult(success=ok, output=f"✓ {app.title()} arrêté de force." if ok else "",
                               error=out if not ok else "")
        script = f"""
$procs = Get-Process -Name '{safe}' -ErrorAction SilentlyContinue
if (-not $procs) {{ "not_found"; exit }}
foreach ($p in $procs) {{ $p.CloseMainWindow() | Out-Null }}
Start-Sleep -Milliseconds 2000
$still = Get-Process -Name '{safe}' -ErrorAction SilentlyContinue
if ($still) {{ Stop-Process -Name '{safe}' -Force -ErrorAction SilentlyContinue; "force_killed" }}
else {{ "graceful" }}
"""
        ok, out = _ps(script, timeout=12)
        if not ok:
            return SkillResult(success=False, output="", error=f"Erreur fermeture : {out}")
        if "not_found" in out:
            # Fallback pywinauto pour les UWP (notepad, calculatrice…)
            def _pw_close():
                win, _ = _find_pywinauto_window(app, pid=None)
                if win is None:
                    return False
                try:
                    win.close()
                    return True
                except Exception:
                    return False
            try:
                found = await asyncio.to_thread(_pw_close)
            except Exception:
                found = False
            if found:
                return SkillResult(success=True, output=f"✓ {app.title()} fermé.")
            return SkillResult(success=False, output="", error=f"'{app}' ne semble pas être ouvert.")
        verb = "fermé proprement" if "graceful" in out else "arrêté"
        return SkillResult(success=True, output=f"✓ {app.title()} {verb}.")

    # ── status ────────────────────────────────────────────────────────────────

    async def _status(self, app: str) -> SkillResult:
        if not app:
            return SkillResult(success=False, output="", error="Paramètre 'app' requis.")
        exe = _resolve_app(app) or app
        safe = _proc_name(exe).replace("'", "").replace(";", "")
        script = f"""
$procs = Get-Process -Name '{safe}' -ErrorAction SilentlyContinue
if (-not $procs) {{ @{{running=$false}} | ConvertTo-Json; exit }}
$p = $procs | Sort-Object CPU -Descending | Select-Object -First 1
@{{
  running=$true; pid=$p.Id; name=$p.ProcessName
  ram_mb=[math]::Round($p.WorkingSet64/1MB,1)
  cpu=[math]::Round($p.CPU,1)
  window=$p.MainWindowTitle; instances=$procs.Count
}} | ConvertTo-Json
"""
        ok, raw = _ps(script)
        if not ok:
            return SkillResult(success=False, output="", error=raw)
        try:
            d = json.loads(raw)
        except Exception:
            return SkillResult(success=True, output=raw)
        if not d.get("running"):
            return SkillResult(success=True, output=f"❌ {app.title()} n'est pas en cours d'exécution.",
                               data={"running": False})
        inst = d.get("instances", 1)
        win = d.get("window", "")
        output = (
            f"✅ {app.title()} est actif{f' ({inst} instances)' if inst > 1 else ''}\n"
            f"   PID : {d.get('pid')} — RAM : {d.get('ram_mb')} MB — CPU : {d.get('cpu')}s"
            + (f"\n   Fenêtre : {win}" if win else "")
        )
        return SkillResult(success=True, output=output, data=d)

    # ── list_running ──────────────────────────────────────────────────────────

    async def _list_running(self) -> SkillResult:
        script = r"""
Get-Process | Where-Object { $_.MainWindowTitle -ne '' -and $_.MainWindowHandle -ne 0 } |
Sort-Object ProcessName |
ForEach-Object {
  [ordered]@{ name=$_.ProcessName; pid=$_.Id; title=$_.MainWindowTitle
               ram_mb=[math]::Round($_.WorkingSet64/1MB,1) }
} | ConvertTo-Json -Depth 2
"""
        ok, raw = _ps(script, timeout=10)
        if not ok:
            return SkillResult(success=False, output="", error=raw)
        try:
            apps = json.loads(raw)
            if isinstance(apps, dict):
                apps = [apps]
        except Exception:
            return SkillResult(success=True, output=raw)
        if not apps:
            return SkillResult(success=True, output="Aucune application avec fenêtre active.", data={"apps": []})
        lines = [f"🖥 Applications ouvertes ({len(apps)}) :\n"]
        for a in apps:
            title = (a.get("title") or "")[:60]
            lines.append(f"  • {a.get('name'):<22} [{a.get('ram_mb'):>7} MB]"
                         + (f"  — {title}" if title else ""))
        return SkillResult(success=True, output="\n".join(lines), data={"apps": apps})

    # ── focus ─────────────────────────────────────────────────────────────────

    async def _focus(self, app: str) -> SkillResult:
        if not app:
            return SkillResult(success=False, output="", error="Paramètre 'app' requis.")
        exe = _resolve_app(app) or app
        safe = _proc_name(exe).replace("'", "").replace(";", "")

        # Méthode 1 : PowerShell SetForegroundWindow (rapide, Win32)
        script = f"""
$allProcs = Get-Process -Name '{safe}' -ErrorAction SilentlyContinue
if (-not $allProcs) {{ "not_found"; exit }}
$proc = $allProcs | Where-Object {{ $_.MainWindowHandle -ne 0 }} | Sort-Object CPU -Descending | Select-Object -First 1
if (-not $proc) {{ "no_window"; exit }}
$hwnd = $proc.MainWindowHandle
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public class WinFocus {{
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
}}
"@ -ErrorAction SilentlyContinue
[WinFocus]::ShowWindow($hwnd, 9) | Out-Null
[WinFocus]::SetForegroundWindow($hwnd) | Out-Null
"ok"
"""
        ok, out = _ps(script, timeout=8)
        if ok and "ok" in out:
            return SkillResult(success=True, output=f"✓ {app.title()} mis au premier plan.")

        # Méthode 2 : pywinauto par titre (UWP / multi-process)
        if "not_found" in out or "no_window" in out or not ok:
            def _pw_focus():
                win, _ = _find_pywinauto_window(app, pid=None)
                if win is None:
                    return False
                try:
                    win.set_focus()
                    return True
                except Exception:
                    return False
            try:
                found = await asyncio.to_thread(_pw_focus)
                if found:
                    return SkillResult(success=True, output=f"✓ {app.title()} mis au premier plan.")
            except Exception:
                pass

        if "not_found" in out:
            return SkillResult(success=False, output="", error=f"'{app}' n'est pas en cours d'exécution.")
        return SkillResult(success=False, output="", error=f"Fenêtre '{app}' introuvable (UWP ou arrière-plan).")

    # ── interact ──────────────────────────────────────────────────────────────

    async def _interact(
        self,
        app: str,
        text: str = "",
        url: str = "",
        keys: str = "",
        send_enter: bool = False,
        element: str = "",
        wait_ms: int = 800,
    ) -> SkillResult:
        if not app:
            return SkillResult(success=False, output="", error="Paramètre 'app' requis.")

        exe = _resolve_app(app) or app
        safe_exe = _proc_name(exe).replace("'", "").replace(";", "")
        profile = _APP_PROFILES.get(safe_exe, _APP_PROFILES.get(exe.lower(), {}))

        # 1. Ouvrir si pas en cours
        status = await self._status(app)
        is_running = status.success and (status.data or {}).get("running")
        if not is_running:
            open_res = await self._open(app)
            if not open_res.success:
                return open_res
            await asyncio.sleep(2.5)  # laisser le temps à l'app de démarrer
        else:
            await asyncio.sleep(max(0.2, wait_ms / 1000))

        # 2. PID pour pywinauto (best-effort, UWP peut être None)
        status2 = await self._status(app)
        pid = (status2.data or {}).get("pid") if status2.success else None

        # 3. Interaction via pywinauto — gère le focus internement (win.set_focus)
        def _pw_run():
            import time
            # Cherche la fenêtre (par PID puis par titre pour UWP)
            win, backend = _find_pywinauto_window(app, pid)
            if win is None:
                return False, "window_not_found"
            try:
                win.set_focus()
                time.sleep(0.35)
            except Exception:
                pass
            return _pw_interact_win(win, backend, text, url, keys, element, send_enter)

        try:
            pw_ok, pw_out = await asyncio.to_thread(_pw_run)
            if pw_ok:
                return SkillResult(
                    success=True,
                    output=pw_out + (" (lancé)" if not is_running else ""),
                )
        except Exception:
            pass

        # 4. Fallback : focus PowerShell + SendKeys
        await self._focus(app)   # best effort
        await asyncio.sleep(0.3)
        sk_ok, sk_out = _sendkeys_interact(text, url, keys, send_enter)
        return SkillResult(
            success=sk_ok,
            output=(sk_out + (" (lancé)" if not is_running else "")) if sk_ok else "",
            error=sk_out if not sk_ok else "",
        )

    # ── dispatch ──────────────────────────────────────────────────────────────

    async def run(
        self,
        action: str,
        app: str = "",
        text: str = "",
        url: str = "",
        keys: str = "",
        send_enter: bool = False,
        element: str = "",
        force: bool = False,
        wait_ms: int = 800,
        **kwargs,
    ) -> SkillResult:
        if action == "open":
            return await self._open(app)
        if action == "close":
            return await self._close(app, force)
        if action == "status":
            return await self._status(app)
        if action == "list_running":
            return await self._list_running()
        if action == "focus":
            return await self._focus(app)
        if action == "interact":
            return await self._interact(app, text, url, keys, send_enter, element, wait_ms)
        return SkillResult(success=False, output="", error=f"Action inconnue : {action}")

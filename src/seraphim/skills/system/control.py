# src/seraphim/skills/system/control.py
"""
Skills de contrôle système Windows :
- Ouvrir une application
- Régler le volume
- Verrouiller / éteindre / redémarrer
- Luminosité (laptops)
"""

import json
import subprocess
from pathlib import Path

from rapidfuzz import fuzz, process
from seraphim.skills.base import BaseSkill, SkillResult

ALIASES_FILE = Path("voices/app_aliases.json")


def _load_aliases() -> dict:
    if ALIASES_FILE.exists():
        return json.loads(ALIASES_FILE.read_text(encoding="utf-8"))
    return {}


def _save_alias(term: str, app: str):
    aliases = _load_aliases()
    aliases[term] = app
    ALIASES_FILE.write_text(json.dumps(aliases, indent=2, ensure_ascii=False), encoding="utf-8")


# ─── Ouvrir une application ───────────────────────────────────────────────────

class OpenAppSkill(BaseSkill):
    name        = "open_app"
    description = "Ouvre une application installée sur Windows par son nom."
    parameters  = {
        "type": "object",
        "properties": {
            "app": {
                "type": "string",
                "description": "Nom de l'application à ouvrir (ex: notepad, chrome, spotify, discord, explorer)"
            }
        },
        "required": ["app"]
    }

    APP_MAP = {
        "notepad":      "notepad.exe",
        "bloc-notes":   "notepad.exe",
        "calculatrice": "calc.exe",
        "calculator":   "calc.exe",
        "explorateur":  "explorer.exe",
        "explorer":     "explorer.exe",
        "chrome":       "chrome.exe",
        "firefox":      "firefox.exe",
        "edge":         "msedge.exe",
        "spotify":      "spotify.exe",
        "discord":      "discord.exe",
        "vscode":       "code",
        "terminal":     "wt.exe",
        "word":         "winword.exe",
        "excel":        "excel.exe",
        "powerpoint":   "powerpnt.exe",
        "paint":        "mspaint.exe",
        "taskmgr":      "taskmgr.exe",
        "gestionnaire": "taskmgr.exe",
    }

    async def run(self, app: str, **kwargs) -> SkillResult:
        app_lower = app.lower().strip()
        aliases   = _load_aliases()

        # 1. Alias appris
        if app_lower in aliases:
            cmd = self.APP_MAP.get(aliases[app_lower], aliases[app_lower])

        # 2. Correspondance exacte
        elif app_lower in self.APP_MAP:
            cmd = self.APP_MAP[app_lower]

        # 3. Fuzzy match + apprentissage
        else:
            match, score, _ = process.extractOne(
                app_lower, self.APP_MAP.keys(), scorer=fuzz.WRatio
            )
            if score >= 70:
                _save_alias(app_lower, match)
                cmd = self.APP_MAP[match]
            else:
                cmd = app  # Tentative directe (ex: "steam", "obs"...)

        try:
            subprocess.Popen(cmd, shell=True)
            return SkillResult(success=True, output=f"✓ {app} ouvert.")
        except Exception as e:
            return SkillResult(success=False, output=f"Impossible d'ouvrir {app}.", error=str(e))


# ─── Volume ───────────────────────────────────────────────────────────────────

class SetVolumeSkill(BaseSkill):
    name        = "set_volume"
    description = "Règle le volume du système entre 0 et 100, ou le coupe (mute)."
    parameters  = {
        "type": "object",
        "properties": {
            "level": {
                "type": "integer",
                "description": "Niveau de volume entre 0 et 100",
                "minimum": 0,
                "maximum": 100
            },
            "mute": {
                "type": "boolean",
                "description": "true pour couper le son, false pour le rétablir"
            }
        }
    }

    async def run(self, level: int = None, mute: bool = None, **kwargs) -> SkillResult:
        try:
            if mute is True:
                script = "(New-Object -ComObject WScript.Shell).SendKeys([char]173)"
                subprocess.run(["powershell", "-Command", script], check=True)
                return SkillResult(success=True, output="🔇 Son coupé.")

            if level is not None:
                level = max(0, min(100, level))
                try:
                    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
                    from ctypes import cast, POINTER
                    from comtypes import CLSCTX_ALL
                    devices   = AudioUtilities.GetSpeakers()
                    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                    volume    = cast(interface, POINTER(IAudioEndpointVolume))
                    volume.SetMasterVolumeLevelScalar(level / 100.0, None)
                    return SkillResult(success=True, output=f"🔊 Volume réglé à {level}%.")
                except ImportError:
                    return SkillResult(success=False, output="pycaw non installé.", error="uv add pycaw")

        except Exception as e:
            return SkillResult(success=False, output="Erreur volume.", error=str(e))


# ─── Contrôle système ─────────────────────────────────────────────────────────

class SystemControlSkill(BaseSkill):
    name        = "system_control"
    description = "Verrouille, éteint ou redémarre Windows."
    parameters  = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["lock", "shutdown", "restart", "sleep"],
                "description": "Action système : lock (verrouiller), shutdown (éteindre), restart (redémarrer), sleep (veille)"
            }
        },
        "required": ["action"]
    }

    COMMANDS = {
        "lock":     "rundll32.exe user32.dll,LockWorkStation",
        "shutdown": "shutdown /s /t 10",
        "restart":  "shutdown /r /t 10",
        "sleep":    "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
    }

    async def run(self, action: str, **kwargs) -> SkillResult:
        cmd = self.COMMANDS.get(action)
        if not cmd:
            return SkillResult(success=False, output=f"Action inconnue : {action}", error="")
        try:
            subprocess.Popen(cmd, shell=True)
            labels = {
                "lock":     "verrouillé",
                "shutdown": "extinction dans 10s",
                "restart":  "redémarrage dans 10s",
                "sleep":    "mise en veille"
            }
            return SkillResult(success=True, output=f"✓ Système {labels[action]}.")
        except Exception as e:
            return SkillResult(success=False, output="Erreur système.", error=str(e))


# ─── Luminosité ───────────────────────────────────────────────────────────────

class SetBrightnessSkill(BaseSkill):
    name        = "set_brightness"
    description = "Règle la luminosité de l'écran entre 0 et 100 (laptop uniquement)."
    parameters  = {
        "type": "object",
        "properties": {
            "level": {
                "type": "integer",
                "description": "Niveau de luminosité entre 0 et 100",
                "minimum": 0,
                "maximum": 100
            }
        },
        "required": ["level"]
    }

    async def run(self, level: int, **kwargs) -> SkillResult:
        level = max(0, min(100, level))
        try:
            script = f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{level})"
            subprocess.run(["powershell", "-Command", script], check=True)
            return SkillResult(success=True, output=f"☀️ Luminosité réglée à {level}%.")
        except Exception as e:
            return SkillResult(success=False, output="Impossible de régler la luminosité.", error=str(e))
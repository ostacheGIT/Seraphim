"""Perception et action sur le bureau Windows.

Observation (rapide, sans GPU) :
  1. Arbre UI Automation de la fenêtre cible (pywinauto) → éléments numérotés
     avec type, nom, valeur et position.
  2. OCR Windows (WinRT) en complément quand l'arbre UIA est pauvre
     (canvas, jeux, certaines applis Electron).
  3. Modèle de vision Ollama (optionnel, lent sur CPU) via describe_screen().

Action : clic / double-clic / clic droit / saisie / raccourcis / défilement,
via pywinauto (SendInput), sur les coordonnées physiques de l'écran.
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import json
import logging
import re
import subprocess
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Types UIA considérés comme actionnables ou informatifs
_INTERACTIVE = {
    "Button", "SplitButton", "Edit", "ComboBox", "CheckBox", "RadioButton",
    "Hyperlink", "ListItem", "MenuItem", "TabItem", "TreeItem", "DataItem",
    "Slider", "Spinner", "MenuBarItem", "HeaderItem",
}
_INFO = {"Text", "Document", "StatusBar"}

_MAX_ELEMENTS = 60
_MAX_TEXT_ELEMENTS = 15
_MAX_OCR_LINES = 50
_MAX_NAME = 50
_MAX_NODES = 900
_WALK_BUDGET_S = 2.5
_OCR_FALLBACK_THRESHOLD = 5

# Fenêtres à ne jamais prendre pour cible (l'interface de Seraphim elle-même)
_SELF_TITLES = {"seraphim"}


# ── Données ──────────────────────────────────────────────────────────────────

@dataclass
class Element:
    id: int
    kind: str
    name: str
    x: int          # centre, coordonnées physiques écran
    y: int
    value: str = ""
    enabled: bool = True
    hint: str = ""        # AutomationId parlant (ex: num7Button)

    def describe(self) -> str:
        s = f"[{self.id}] {self.kind} {self.name}"
        if self.hint:
            s += f" ({self.hint})"
        if self.value:
            s += f" = {self.value}"
        if not self.enabled:
            s += " (désactivé)"
        return s


@dataclass
class Observation:
    window_title: str
    window_hwnd: int
    windows: list[str]
    elements: list[Element] = field(default_factory=list)
    used_ocr: bool = False

    def element(self, eid: int | None) -> Element | None:
        if eid is None:
            return None
        return next((e for e in self.elements if e.id == eid), None)

    def to_prompt(self) -> str:
        lines = [f"Fenêtre active : « {self.window_title or '(aucune)'} »"]
        others = [w[:60] for w in self.windows if w != self.window_title][:8]
        if others:
            lines.append("Autres fenêtres ouvertes : " + " | ".join(f"« {w} »" for w in others))
        if self.elements:
            src = "UI Automation + OCR" if self.used_ocr else "UI Automation"
            lines.append(f"Éléments visibles ({src}) :")
            lines.extend(e.describe() for e in self.elements)
        else:
            lines.append("Aucun élément détecté dans cette fenêtre.")
        return "\n".join(lines)


# ── Fenêtres ─────────────────────────────────────────────────────────────────

def _is_cloaked(hwnd: int) -> bool:
    cloaked = ctypes.c_int(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)  # DWMWA_CLOAKED
        )
    except Exception:
        return False
    return cloaked.value != 0


def list_windows() -> list[tuple[int, str]]:
    """Fenêtres de premier niveau visibles, dans l'ordre Z (du dessus vers le dessous)."""
    import win32con
    import win32gui

    out: list[tuple[int, str]] = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return True
        ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if ex & win32con.WS_EX_TOOLWINDOW:
            return True
        if _is_cloaked(hwnd):
            return True
        if title in ("Program Manager", "Paramètres de l’expérience d’entrée", "Expérience d’entrée Windows"):
            return True
        out.append((hwnd, title))
        return True

    win32gui.EnumWindows(_cb, None)
    return out


def _is_self(title: str) -> bool:
    return title.strip().lower() in _SELF_TITLES


def pick_target_window(preferred_hwnd: int | None = None) -> tuple[int, str, list[str]]:
    """Choisit la fenêtre sur laquelle agir.

    1. La fenêtre de travail déjà choisie (`preferred_hwnd`) si elle existe encore :
       l'agent reste dessus même si l'utilisateur clique ailleurs entre-temps.
    2. Sinon la fenêtre au premier plan, sauf si c'est Seraphim lui-même.
    3. Sinon la première fenêtre (ordre Z) qui n'est pas Seraphim."""
    import win32gui

    wins = list_windows()
    titles = [t for _, t in wins]

    if preferred_hwnd and win32gui.IsWindow(preferred_hwnd) and win32gui.IsWindowVisible(preferred_hwnd):
        title = win32gui.GetWindowText(preferred_hwnd).strip()
        if title and not _is_self(title):
            return preferred_hwnd, title, titles

    fg = win32gui.GetForegroundWindow()
    fg_title = win32gui.GetWindowText(fg).strip() if fg else ""
    if fg and fg_title and not _is_self(fg_title):
        return fg, fg_title, titles
    for hwnd, title in wins:
        if not _is_self(title):
            return hwnd, title, titles
    return 0, "", titles


def focus_window(hwnd: int) -> bool:
    try:
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_element_info import UIAElementInfo

        w = UIAWrapper(UIAElementInfo(hwnd))
        if w.is_minimized():
            w.restore()
        w.set_focus()
        time.sleep(0.3)
        return True
    except Exception as exc:
        logger.debug("focus_window(%s) failed: %s", hwnd, exc)
        return False


def find_window(title_part: str) -> tuple[int, str] | None:
    needle = title_part.lower().strip()
    if not needle:
        return None
    for hwnd, title in list_windows():
        if needle in title.lower() and not _is_self(title):
            return hwnd, title
    try:
        from rapidfuzz import fuzz, process

        wins = [(h, t) for h, t in list_windows() if not _is_self(t)]
        if wins:
            match = process.extractOne(needle, [t for _, t in wins], scorer=fuzz.partial_ratio)
            if match and match[1] >= 75:
                return wins[match[2]]
    except Exception:
        pass
    return None


# ── Observation UIA ──────────────────────────────────────────────────────────

def _screen_bounds() -> tuple[int, int, int, int]:
    u = ctypes.windll.user32
    x, y = u.GetSystemMetrics(76), u.GetSystemMetrics(77)      # SM_XVIRTUALSCREEN…
    w, h = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
    return x, y, x + w, y + h


_HINT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,24}$")


def _hint(info, name: str) -> str:
    """AutomationId si il apporte une info (identifiant lisible, différent du nom)."""
    try:
        aid = info.automation_id or ""
    except Exception:
        return ""
    if not _HINT_RE.match(aid) or aid.lower() == name.lower().replace(" ", ""):
        return ""
    if not re.search(r"[a-z]{3}", aid):    # écarte les identifiants opaques
        return ""
    return aid


def _uia_elements(hwnd: int) -> list[Element]:
    from pywinauto.uia_element_info import UIAElementInfo

    sx0, sy0, sx1, sy1 = _screen_bounds()
    root = UIAElementInfo(hwnd)
    try:
        wr = root.rectangle
        wx0, wy0, wx1, wy1 = wr.left, wr.top, wr.right, wr.bottom
    except Exception:
        wx0, wy0, wx1, wy1 = sx0, sy0, sx1, sy1

    elements: list[Element] = []
    seen: set[tuple] = set()
    n_text = 0
    visited = 0
    deadline = time.monotonic() + _WALK_BUDGET_S
    queue: deque = deque([root])

    while queue and visited < _MAX_NODES and time.monotonic() < deadline:
        info = queue.popleft()
        visited += 1
        try:
            children = info.children()
        except Exception:
            children = []
        queue.extend(children)
        if info is root:
            continue
        try:
            kind = info.control_type or ""
            if kind not in _INTERACTIVE and kind not in _INFO:
                continue
            name = (info.name or "").strip().replace("\n", " ")
            r = info.rectangle
            if r.width() <= 1 or r.height() <= 1:
                continue
            cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
            if not (max(wx0, sx0) <= cx < min(wx1, sx1) and max(wy0, sy0) <= cy < min(wy1, sy1)):
                continue
            value = ""
            if kind in ("Edit", "ComboBox"):
                try:
                    from pywinauto.controls.uiawrapper import UIAWrapper
                    value = (UIAWrapper(info).iface_value.CurrentValue or "")[:60]
                except Exception:
                    value = ""
            if not name and not value and kind not in ("Edit", "ComboBox"):
                continue
            if kind in _INFO:
                if n_text >= _MAX_TEXT_ELEMENTS or not name:
                    continue
                n_text += 1
            key = (kind, name, cx // 4, cy // 4)
            if key in seen:
                continue
            seen.add(key)
            try:
                enabled = bool(info.enabled)
            except Exception:
                enabled = True
            elements.append(Element(
                id=len(elements) + 1, kind=kind, name=name[:_MAX_NAME] or "(sans nom)",
                x=cx, y=cy, value=value, enabled=enabled, hint=_hint(info, name),
            ))
            if len(elements) >= _MAX_ELEMENTS:
                break
        except Exception:
            continue
    return elements


# ── Capture d'écran + OCR (PowerShell, DPI-aware) ────────────────────────────

_PS_DPI = (
    "Add-Type -Name DpiU -Namespace SeraphimW -MemberDefinition "
    "'[DllImport(\"user32.dll\")] public static extern bool SetProcessDPIAware();' "
    "-ErrorAction SilentlyContinue; [SeraphimW.DpiU]::SetProcessDPIAware() | Out-Null\n"
)

_PS_CAPTURE = _PS_DPI + r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
$bmp.Save($OutPath, [System.Drawing.Imaging.ImageFormat]::Png)
if ($SmallPath) {
  $ratio = [Math]::Min(1.0, $MaxW / $b.Width)
  $sw = [int]($b.Width * $ratio); $sh = [int]($b.Height * $ratio)
  $small = New-Object System.Drawing.Bitmap($sw, $sh)
  $g2 = [System.Drawing.Graphics]::FromImage($small)
  $g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $g2.DrawImage($bmp, 0, 0, $sw, $sh)
  $small.Save($SmallPath, [System.Drawing.Imaging.ImageFormat]::Jpeg)
  $g2.Dispose(); $small.Dispose()
}
$g.Dispose(); $bmp.Dispose()
"{0},{1}" -f $b.Left, $b.Top
"""

_PS_OCR_LINES = r"""
[void][System.Reflection.Assembly]::LoadWithPartialName("System.Runtime.WindowsRuntime")
$null = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics,ContentType=WindowsRuntime]
$asTaskG = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function AwaitT($Op, $Type) {
  $t = $asTaskG.MakeGenericMethod($Type).Invoke($null, @($Op)); $t.Wait(-1) | Out-Null; $t.Result
}
$file    = AwaitT ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
$stream  = AwaitT ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = AwaitT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap  = AwaitT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine  = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { Write-Error "OCR indisponible"; exit 1 }
$res = AwaitT ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$lines = foreach ($l in $res.Lines) {
  $x0 = 1e9; $y0 = 1e9; $x1 = 0; $y1 = 0
  foreach ($w in $l.Words) { $r = $w.BoundingRect
    $x0 = [Math]::Min($x0, $r.X); $y0 = [Math]::Min($y0, $r.Y)
    $x1 = [Math]::Max($x1, $r.X + $r.Width); $y1 = [Math]::Max($y1, $r.Y + $r.Height) }
  [pscustomobject]@{ t = $l.Text; x = [int](($x0 + $x1) / 2); y = [int](($y0 + $y1) / 2) }
}
$stream.Dispose()
ConvertTo-Json -InputObject @($lines) -Compress
"""


def _ps(script: str, timeout: int = 30) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n" + script],
            capture_output=True, timeout=timeout,
        )
        out = (r.stdout or b"").decode("utf-8", errors="replace").strip()
        err = (r.stderr or b"").decode("utf-8", errors="replace").strip()
        return r.returncode == 0, out if r.returncode == 0 else (err or out)
    except subprocess.TimeoutExpired:
        return False, f"Timeout {timeout}s"
    except Exception as exc:
        return False, str(exc)


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def capture_screen(small_max_width: int = 0) -> tuple[str, str, tuple[int, int]]:
    """Capture tout le bureau virtuel (pixels physiques).
    Retourne (png_path, small_jpg_path|"", (origin_x, origin_y))."""
    tmp = Path(tempfile.gettempdir())
    stamp = int(time.time() * 1000)
    png = tmp / f"seraphim_desk_{stamp}.png"
    small = tmp / f"seraphim_desk_{stamp}_small.jpg" if small_max_width else None
    script = (
        f"$OutPath = {_ps_quote(str(png))}\n"
        f"$SmallPath = {_ps_quote(str(small)) if small else '$null'}\n"
        f"$MaxW = {small_max_width or 1280}\n" + _PS_CAPTURE
    )
    ok, out = _ps(script, timeout=20)
    if not ok or not png.exists():
        raise RuntimeError(f"Capture d'écran impossible : {out}")
    try:
        ox, oy = (int(v) for v in out.splitlines()[-1].split(","))
    except Exception:
        ox, oy = 0, 0
    return str(png), (str(small) if small and small.exists() else ""), (ox, oy)


def ocr_lines(png_path: str, origin: tuple[int, int] = (0, 0)) -> list[tuple[str, int, int]]:
    script = f"$ImagePath = {_ps_quote(str(Path(png_path).resolve()))}\n" + _PS_OCR_LINES
    ok, out = _ps(script, timeout=40)
    if not ok or not out:
        logger.debug("OCR failed: %s", out)
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    ox, oy = origin
    return [(d["t"], d["x"] + ox, d["y"] + oy) for d in data if d and d.get("t")]


def _cleanup(*paths: str) -> None:
    for p in paths:
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass


# ── API publique : observer ──────────────────────────────────────────────────

def observe(preferred_hwnd: int | None = None, force_ocr: bool = False) -> Observation:
    import pywinauto  # noqa: F401 — rend le process DPI-aware (coordonnées physiques)
    import win32gui

    hwnd, title, titles = pick_target_window(preferred_hwnd)
    obs = Observation(window_title=title, window_hwnd=hwnd, windows=titles)
    if hwnd:
        try:
            obs.elements = _uia_elements(hwnd)
        except Exception as exc:
            logger.debug("UIA walk failed: %s", exc)

    if force_ocr or len(obs.elements) < _OCR_FALLBACK_THRESHOLD:
        png = ""
        try:
            png, _, origin = capture_screen()
            try:
                l, t, r, b = win32gui.GetWindowRect(hwnd) if hwnd else _screen_bounds()
            except Exception:
                l, t, r, b = _screen_bounds()
            n_ocr = 0
            for text, x, y in ocr_lines(png, origin):
                if not (l <= x < r and t <= y < b):
                    continue
                if n_ocr >= _MAX_OCR_LINES:
                    break
                n_ocr += 1
                obs.elements.append(Element(
                    id=len(obs.elements) + 1, kind="Texte", name=text[:_MAX_NAME], x=x, y=y,
                ))
            obs.used_ocr = True
        except Exception as exc:
            logger.debug("OCR fallback failed: %s", exc)
        finally:
            _cleanup(png)
    return obs


def screen_text() -> str:
    """Texte complet visible à l'écran (OCR)."""
    png = ""
    try:
        png, _, origin = capture_screen()
        return "\n".join(t for t, _, _ in ocr_lines(png, origin))
    finally:
        _cleanup(png)


async def describe_screen(question: str, vision_model: str, base_url: str, timeout: float = 240) -> str:
    """Analyse visuelle via un modèle de vision Ollama (ex: qwen2.5vl:3b, llava)."""
    import httpx

    png = small = ""
    try:
        png, small, _ = capture_screen(small_max_width=1280)
        img = base64.b64encode(Path(small or png).read_bytes()).decode()
        payload = {
            "model": vision_model,
            "stream": False,
            "messages": [{
                "role": "user",
                "content": (question or "Décris précisément ce qui est affiché à l'écran.")
                + "\nRéponds en français, de façon factuelle et concise.",
                "images": [img],
            }],
            "options": {"temperature": 0.1},
        }
        async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout) as c:
            r = await c.post("/api/chat", json=payload)
            r.raise_for_status()
            return (r.json().get("message", {}).get("content") or "").strip()
    finally:
        _cleanup(png, small)


async def vision_model_available(model: str, base_url: str) -> bool:
    if not model:
        return False
    import httpx

    try:
        async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=5) as c:
            r = await c.get("/api/tags")
            names = {m.get("name", "") for m in r.json().get("models", [])}
    except Exception:
        return False
    return model in names or f"{model}:latest" in names


# ── Actions ──────────────────────────────────────────────────────────────────

_NAMED_KEYS = {
    "enter": "{ENTER}", "entrée": "{ENTER}", "return": "{ENTER}",
    "tab": "{TAB}", "esc": "{ESC}", "escape": "{ESC}", "échap": "{ESC}",
    "backspace": "{BACKSPACE}", "retour": "{BACKSPACE}",
    "delete": "{DELETE}", "del": "{DELETE}", "suppr": "{DELETE}",
    "up": "{UP}", "down": "{DOWN}", "left": "{LEFT}", "right": "{RIGHT}",
    "haut": "{UP}", "bas": "{DOWN}", "gauche": "{LEFT}", "droite": "{RIGHT}",
    "home": "{HOME}", "end": "{END}", "pageup": "{PGUP}", "pagedown": "{PGDN}",
    "pgup": "{PGUP}", "pgdn": "{PGDN}", "space": "{SPACE}", "espace": "{SPACE}",
    "insert": "{INSERT}", "printscreen": "{PRTSC}",
}
_MODIFIERS = {"ctrl": "^", "control": "^", "ctl": "^", "alt": "%", "shift": "+", "maj": "+"}
_WIN_MODS = {"win", "windows", "super", "meta", "cmd"}
_SPECIAL_CHARS = set("{}+^%~()[]")


def escape_text(text: str) -> str:
    """Échappe un texte libre pour pywinauto.keyboard.send_keys."""
    out = []
    for ch in text:
        if ch in _SPECIAL_CHARS:
            out.append("{" + ch + "}")
        elif ch == "\n":
            out.append("{ENTER}")
        elif ch == "\t":
            out.append("{TAB}")
        elif ch == "\r":
            continue
        else:
            out.append(ch)
    return "".join(out)


def keys_to_pywinauto(spec: str) -> str:
    """'ctrl+shift+t' → '^+t' ; 'enter' → '{ENTER}' ; 'win+d' ; 'ctrl+l, enter'."""
    seq = []
    for combo in [c.strip() for c in spec.replace(";", ",").split(",") if c.strip()]:
        parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
        if not parts:
            continue
        *mods, key = parts
        win = any(m in _WIN_MODS for m in mods)
        prefix = "".join(_MODIFIERS[m] for m in mods if m in _MODIFIERS)
        unknown = [m for m in mods if m not in _MODIFIERS and m not in _WIN_MODS]
        if unknown:
            raise ValueError(f"Modificateur inconnu : {unknown[0]}")
        if key in _NAMED_KEYS:
            k = _NAMED_KEYS[key]
        elif len(key) >= 2 and key[0] == "f" and key[1:].isdigit():
            k = "{" + key.upper() + "}"
        elif key in _WIN_MODS and not mods:
            seq.append("{VK_LWIN}")
            continue
        elif len(key) == 1:
            k = escape_text(key)
        else:
            raise ValueError(f"Touche inconnue : {key}")
        s = prefix + k
        if win:
            s = "{VK_LWIN down}" + s + "{VK_LWIN up}"
        seq.append(s)
    if not seq:
        raise ValueError("Aucune touche fournie.")
    return "".join(seq)


def emergency_stop_requested() -> bool:
    """Arrêt d'urgence : souris placée dans le coin supérieur gauche de l'écran."""
    pt = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    x0, y0, _, _ = _screen_bounds()
    return pt.x <= x0 + 2 and pt.y <= y0 + 2


def click(x: int, y: int, button: str = "left", double: bool = False) -> None:
    from pywinauto import mouse

    if double:
        mouse.double_click(button=button, coords=(x, y))
    else:
        mouse.click(button=button, coords=(x, y))


def type_text(text: str) -> None:
    from pywinauto import keyboard

    keyboard.send_keys(escape_text(text), with_spaces=True, with_tabs=True,
                       with_newlines=True, pause=0.01)


def press_keys(spec: str) -> None:
    from pywinauto import keyboard

    keyboard.send_keys(keys_to_pywinauto(spec), pause=0.05)


def scroll(x: int, y: int, direction: str, amount: int = 5) -> None:
    from pywinauto import mouse

    dist = amount if direction.lower() in ("up", "haut") else -amount
    mouse.scroll(coords=(x, y), wheel_dist=dist)


def window_center(hwnd: int) -> tuple[int, int]:
    import win32gui

    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        return (l + r) // 2, (t + b) // 2
    except Exception:
        x0, y0, x1, y1 = _screen_bounds()
        return (x0 + x1) // 2, (y0 + y1) // 2

"""Trouver n'importe quoi de visible à l'écran et l'activer (clic, ouverture).

Moyen GÉNÉRAL, sans code spécifique à une application : on cherche la cible par
plusieurs sources, de la plus fiable à la plus approximative, puis on essaie
plusieurs façons de l'activer en vérifiant à chaque fois que l'écran a réagi.

Localisation (dans l'ordre) :
  1. UI Automation sur TOUTES les fenêtres visibles (fenêtre de travail d'abord),
     la barre des tâches et le bureau → élément nommé, actionnable même s'il est
     recouvert par une autre fenêtre.
  2. OCR Windows de tout l'écran, mot par mot → position exacte d'un texte affiché
     (applis sans arbre UIA : jeux, canvas, IDE Java…).
  3. Modèle de vision (qwen2.5vl) → position d'une chose décrite (icône, image…).
  La fenêtre de Seraphim lui-même est toujours exclue (sinon on trouverait le
  message de l'utilisateur dans le chat).

Activation (dans l'ordre, jusqu'à ce que l'écran change) :
  - élément UIA : motif natif (Invoke / Select / Expand), puis souris
  - « ouvrir » : double-clic pour les éléments de liste/arbre (fichiers, icônes),
    Invoke pour les liens et boutons, puis double-clic
  - position (OCR / vision) : souris (simple ou double clic)
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import logging
import re
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field

from seraphim.desktop import computer as pc

logger = logging.getLogger(__name__)

MODES = ("click", "double", "right", "open")

_MIN_SCORE = 80
_UIA_BUDGET_S = 4.0
_UIA_MAX_NODES_PER_WINDOW = 2500
_SKIP_KINDS = {"Window", "Pane", "TitleBar", "ScrollBar", "Thumb", "Separator", "ToolTip"}
_INTERACTIVE = pc._INTERACTIVE | {"Image", "Group", "Custom"}
_LIST_KINDS = {"ListItem", "TreeItem", "DataItem"}
_INVOKE_KINDS = {"Button", "SplitButton", "Hyperlink", "MenuItem", "MenuBarItem"}

# Mots génériques à retirer de la cible : « le bouton Paramètres » → « Paramètres »
_GENERIC_RE = re.compile(
    # pas de « ce / cette » : « Ce PC » est un nom propre de Windows
    r"^(?:(?:sur|dans)\s+)?(?:(?:le|la|les|l|un|une|du|de\s+la|des|mon|ma|mes|ton|ta)\s+|l['’]\s*)?"
    r"(?:(?:bouton|lien|fichier|dossier|onglet|ic[ôo]ne|menu|option|case|champ|élément|element|"
    r"raccourci|application|appli|programme|vid[ée]o|image|article|page|r[ée]sultat|mail|message|"
    r"button|link|file|folder|tab|icon|item)s?\s+)?",
    re.I,
)


@dataclass
class Candidate:
    source: str                 # "uia" | "ocr" | "vision"
    name: str
    x: int
    y: int
    score: float
    hwnd: int = 0               # fenêtre de premier niveau propriétaire (0 = inconnue)
    kind: str = ""
    info: object | None = field(default=None, repr=False)   # UIAElementInfo


@dataclass
class Outcome:
    success: bool
    message: str
    candidate: Candidate | None = None
    method: str = ""


# ── Texte ────────────────────────────────────────────────────────────────────

def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[«»\"'`“”’]", " ", s.lower())
    s = re.sub(r"[^\w.+#@/-]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# « … que tu vois dans la fenêtre Explorer », « … à l'écran », « … sur le bureau »
# « que tu vois », mais aussi « et tu vois » (que mal entendu par la dictée)
_RELATIVE_RE = re.compile(
    r"\s+(?:que|qu['’]|qui|et\s+(?:que\s+)?(?:tu|vous|on)\s+(?:vois|voyez|voit))\b.*$", re.I)
_LOCATION_RE = re.compile(
    r"\s+(?:dans|sur|à|a|de)\s+(?:la\s+|le\s+|l['’]\s*|l\s+|cette\s+|ce\s+|mon\s+|ma\s+|ton\s+|ta\s+)?"
    r"(?P<where>(?:fen[êe]tre|[ée]cran|bureau|explorateur|explorer|windows|barre|page|onglet|appli(?:cation)?)\b.*)$",
    re.I,
)
_WINDOW_WORD_RE = re.compile(r"^(?:fen[êe]tre|appli(?:cation)?|onglet|page)\s*(?:de\s+|d['’]\s*)?", re.I)


def split_location(target: str) -> tuple[str, str]:
    """« README.md que tu vois dans la fenêtre windows explorer » →
    (« README.md », « windows explorer ») ; la 2e partie aide à choisir la fenêtre."""
    t = (target or "").strip()
    hint = ""
    m = _LOCATION_RE.search(t)
    if m:
        hint = _WINDOW_WORD_RE.sub("", m.group("where")).strip()
        t = t[:m.start()]
    t = _RELATIVE_RE.sub("", t)
    m = _LOCATION_RE.search(t)                       # « … dans la fenêtre … que tu vois »
    if m:
        hint = hint or _WINDOW_WORD_RE.sub("", m.group("where")).strip()
        t = t[:m.start()]
    if normalize(hint) in ("ecran", "l ecran", "mon ecran"):
        hint = ""
    return t.strip(), hint


def window_for_hint(hint: str) -> int | None:
    """Fenêtre désignée par l'utilisateur (« explorer », « chrome », « bureau »…)."""
    import win32gui

    h = normalize(hint)
    if not h:
        return None
    if h.startswith("bureau") or h.startswith("desktop"):
        return win32gui.FindWindow("Progman", None) or None
    if h.startswith("barre"):
        return win32gui.FindWindow("Shell_TrayWnd", None) or None
    if "explor" in h:                                   # Explorateur de fichiers Windows
        for hwnd, _ in pc.list_windows():
            if win32gui.GetClassName(hwnd) == "CabinetWClass":
                return hwnd
    found = pc.find_window(hint)
    return found[0] if found else None


def clean_target(target: str) -> str:
    """« sur le bouton « Paramètres » que tu vois à l'écran » → « Paramètres »."""
    t = split_location(target)[0].strip().strip(".!?")
    t = _GENERIC_RE.sub("", t, count=1).strip()
    if len(t) > 1 and t[0] in "«\"'“" and t[-1] in "»\"'”":
        t = t[1:-1].strip()
    return t or (target or "").strip()


_DRIVE_RE = re.compile(
    r"\s*(?:(?:le|mon|ton)\s+)?(?:(?:disque|lecteur|drive|volume)\s+(?:local\s+)?)?\(?([a-zA-Z]):\\?\)?\s*|"
    r"\s*(?:(?:le|mon|ton)\s+)?(?:disque|lecteur|drive|volume)\s+(?:local\s+)?([a-zA-Z])\s*",
    re.I,
)


def match_score(needle: str, text: str) -> float:
    """0-100 : à quel point `text` (affiché) correspond à la cible `needle`."""
    from rapidfuzz import fuzz

    # Lecteur : « disque D: », « lecteur D », « D: » ↔ « Samsung SSD (D:) »
    dm = _DRIVE_RE.fullmatch(needle or "")
    if dm:
        letter = dm.group(1) or dm.group(2)
        return 95.0 if re.search(rf"\(\s*{letter}:\s*\)|^{letter}:\\?$", text or "", re.I) else 0.0

    n, t = normalize(needle), normalize(text)
    if not n or not t:
        return 0.0
    if n == t:
        return 100.0
    if len(n) >= 3 and (t.startswith(n) or t.endswith(n)):
        return 93.0 if len(t) <= 2 * len(n) + 4 else 86.0
    if len(n) >= 3 and re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", t):
        return 90.0 if len(t) <= 3 * len(n) else 82.0
    if len(n) >= 4 and n in t:
        return 80.0 if len(t) <= 3 * len(n) else 70.0
    if abs(len(n) - len(t)) <= max(2, len(n) // 4):
        return float(fuzz.ratio(n, t)) * 0.95          # fautes d'OCR / de frappe
    return 0.0


# ── Fenêtres à fouiller / à exclure ──────────────────────────────────────────

def _is_seraphim(hwnd: int) -> bool:
    import win32gui

    try:
        return (win32gui.GetClassName(hwnd) == "Tauri Window"
                or pc._is_self(win32gui.GetWindowText(hwnd)))
    except Exception:
        return False


def seraphim_rects() -> list[tuple[int, int, int, int]]:
    import win32gui

    rects = []
    for hwnd, _ in pc.list_windows():
        if _is_seraphim(hwnd) and not win32gui.IsIconic(hwnd):
            rects.append(win32gui.GetWindowRect(hwnd))
    return rects


def _in_rects(x: int, y: int, rects) -> bool:
    return any(left <= x < right and top <= y < bottom for left, top, right, bottom in rects)


_POPUP_CLASSES = {
    "#32768",                                        # menu classique Win32
    "Microsoft.UI.Content.PopupWindowSiteBridge",    # menu contextuel Windows 11 (« Hôte contextuel »)
    "Xaml_WindowedPopupClass", "Windows.UI.Core.CoreWindow.Popup",
    "DropDown", "ComboLBox", "SysDropDown",
}


def open_popups() -> list[int]:
    """Menus et listes déroulantes ouverts (fenêtres-outils sans entrée dans la liste
    des fenêtres), du dessus vers le dessous."""
    import win32gui

    found: list[int] = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) in _POPUP_CLASSES:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            if right - left > 20 and bottom - top > 20:
                found.append(hwnd)
        return True

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    return found


def search_windows(preferred_hwnd: int | None = None) -> list[int]:
    """Fenêtre de travail d'abord, puis les autres fenêtres visibles (ordre Z),
    la barre des tâches et le bureau. Jamais Seraphim, jamais une fenêtre réduite."""
    import win32gui

    first, _, _ = pc.pick_target_window(preferred_hwnd)
    out = open_popups()                      # un menu qui vient de s'ouvrir passe en premier
    if first and first not in out:
        out.append(first)
    for hwnd, _ in pc.list_windows():
        if hwnd not in out and not _is_seraphim(hwnd) and not win32gui.IsIconic(hwnd):
            out.append(hwnd)
    for cls in ("Shell_TrayWnd", "Progman"):
        h = win32gui.FindWindow(cls, None)
        if h and h not in out:
            out.append(h)
    return out


# ── 1. UI Automation, toutes fenêtres ────────────────────────────────────────

def uia_search(needle: str, preferred_hwnd: int | None = None,
               budget_s: float = _UIA_BUDGET_S) -> list[Candidate]:
    import pywinauto  # noqa: F401 — DPI-aware
    from pywinauto.uia_element_info import UIAElementInfo

    sx0, sy0, sx1, sy1 = pc._screen_bounds()
    deadline = time.monotonic() + budget_s
    found: list[Candidate] = []

    for rank, hwnd in enumerate(search_windows(preferred_hwnd)):
        if time.monotonic() > deadline:
            break
        try:
            root = UIAElementInfo(hwnd)
        except Exception:
            continue
        queue, visited = deque([root]), 0
        while queue and visited < _UIA_MAX_NODES_PER_WINDOW and time.monotonic() < deadline:
            info = queue.popleft()
            visited += 1
            with contextlib.suppress(Exception):
                queue.extend(info.children())
            if info is root:
                continue
            try:
                kind = info.control_type or ""
                if kind in _SKIP_KINDS:
                    continue
                name = (info.name or "").strip()
                if not name:
                    continue
                score = match_score(needle, name)
                if score < _MIN_SCORE:
                    continue
                r = info.rectangle
                if r.width() <= 1 or r.height() <= 1:
                    continue
                cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
                if not (sx0 <= cx < sx1 and sy0 <= cy < sy1):
                    continue
                try:
                    if info.element.CurrentIsOffscreen:
                        continue
                except Exception:
                    pass
                if kind in _INTERACTIVE:
                    score += 3
                if kind in _LIST_KINDS:        # un fichier / dossier / disque plutôt qu'un bouton
                    score += 2                 # homonyme (ex : fil d'Ariane de la barre d'adresse)
                score -= min(rank, 5)          # menu ouvert, puis fenêtre de travail, d'abord
                found.append(Candidate("uia", name[:80], cx, cy, score, hwnd, kind, info))
            except Exception:
                continue
        # Correspondance exacte dans la fenêtre de travail : inutile d'aller plus loin
        if rank == 0 and any(c.score >= 100 for c in found):
            break
    found.sort(key=lambda c: -c.score)
    return found


# ── 2. OCR de tout l'écran, mot par mot ──────────────────────────────────────

_PS_OCR_WORDS = pc._PS_OCR_LINES.split("$lines = foreach")[0] + r"""
$out = foreach ($l in $res.Lines) {
  $ws = foreach ($w in $l.Words) { $r = $w.BoundingRect
    [pscustomobject]@{ t = $w.Text; x = [int]$r.X; y = [int]$r.Y; w = [int]$r.Width; h = [int]$r.Height } }
  [pscustomobject]@{ w = @($ws) }
}
$stream.Dispose()
ConvertTo-Json -InputObject @($out) -Compress -Depth 5
"""


def ocr_words(png_path: str) -> list[list[dict]]:
    """Lignes de mots {t, x, y, w, h} (pixels de l'image)."""
    from pathlib import Path

    script = f"$ImagePath = {pc._ps_quote(str(Path(png_path).resolve()))}\n" + _PS_OCR_WORDS
    ok, out = pc._ps(script, timeout=40)
    if not ok or not out:
        logger.debug("OCR words failed: %s", out)
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    lines = []
    for line in data if isinstance(data, list) else [data]:
        words = (line or {}).get("w") or []
        if isinstance(words, dict):
            words = [words]
        lines.append([w for w in words if w and w.get("t")])
    return lines


def ocr_search(needle: str) -> list[Candidate]:
    import tempfile
    from pathlib import Path

    img, (ox, oy) = pc.grab(None)
    tmp = Path(tempfile.gettempdir()) / f"seraphim_find_{int(time.time() * 1000)}.png"
    img.save(tmp)
    try:
        lines = ocr_words(str(tmp))
    finally:
        tmp.unlink(missing_ok=True)

    excluded = seraphim_rects()
    n_words = max(1, len(normalize(needle).split()))
    found: list[Candidate] = []
    for words in lines:
        for i in range(len(words)):
            for size in {n_words - 1, n_words, n_words + 1}:
                if size < 1 or i + size > len(words):
                    continue
                span = words[i:i + size]
                text = " ".join(w["t"] for w in span)
                score = match_score(needle, text)
                if score < _MIN_SCORE:
                    continue
                x0 = min(w["x"] for w in span)
                y0 = min(w["y"] for w in span)
                x1 = max(w["x"] + w["w"] for w in span)
                y1 = max(w["y"] + w["h"] for w in span)
                cx, cy = ox + (x0 + x1) // 2, oy + (y0 + y1) // 2
                if _in_rects(cx, cy, excluded):
                    continue
                found.append(Candidate("ocr", text[:80], cx, cy, score - 2))
    found.sort(key=lambda c: -c.score)
    return found


# ── 3. Modèle de vision ──────────────────────────────────────────────────────

_VISION_SCHEMA = {
    "type": "object",
    "properties": {"found": {"type": "boolean"}, "x": {"type": "integer"}, "y": {"type": "integer"}},
    "required": ["found"],
}


async def vision_search(needle: str, model: str, base_url: str, max_width: int = 1280) -> list[Candidate]:
    import httpx
    from PIL import Image, ImageDraw

    img, (ox, oy) = pc.grab(None)
    img = img.convert("RGB")
    draw = ImageDraw.Draw(img)
    for left, top, right, bottom in seraphim_rects():       # masque la fenêtre de Seraphim
        draw.rectangle((left - ox, top - oy, right - ox, bottom - oy), fill=(40, 40, 40))
    scale = min(1.0, max_width / img.width)
    if scale < 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)

    payload = {
        "model": model, "stream": False, "format": _VISION_SCHEMA, "keep_alive": "30m",
        "options": {"temperature": 0.0, "num_predict": 60, "num_ctx": 8192},
        "messages": [{
            "role": "user", "images": [base64.b64encode(buf.getvalue()).decode()],
            "content": (f"Capture d'écran de {img.width}×{img.height} pixels. Où se trouve « {needle} » ? "
                        "Donne le centre de l'élément en pixels de cette image : "
                        '{"found": true, "x": ..., "y": ...}, ou {"found": false} s\'il n\'est pas visible.'),
        }],
    }
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=180) as c:
        r = await c.post("/api/chat", json=payload)
        r.raise_for_status()
        data = json.loads(r.json().get("message", {}).get("content") or "{}")
    if not data.get("found") or data.get("x") is None or data.get("y") is None:
        return []
    x = min(max(int(data["x"]), 0), img.width - 1)
    y = min(max(int(data["y"]), 0), img.height - 1)
    sx, sy = int(ox + x / scale), int(oy + y / scale)
    if _in_rects(sx, sy, seraphim_rects()):
        return []
    return [Candidate("vision", needle, sx, sy, 60)]


# ── Vérification : l'écran a-t-il réagi ? ────────────────────────────────────

def screen_signature() -> tuple:
    import win32gui
    from PIL import ImageGrab

    fg = win32gui.GetForegroundWindow()
    titles = tuple(sorted(t for _, t in pc.list_windows()))
    thumb = ImageGrab.grab(all_screens=True).convert("L").resize((96, 54)).tobytes()
    return fg, win32gui.GetWindowText(fg), titles, thumb


def screen_changed(before: tuple, after: tuple) -> bool:
    if before[:3] != after[:3]:
        return True
    a, b = before[3], after[3]
    diff = sum(abs(x - y) for x, y in zip(a, b, strict=False)) / max(1, len(a))
    return diff > 1.5


# ── Activation ───────────────────────────────────────────────────────────────

def _owner_root(x: int, y: int) -> int:
    import ctypes

    import win32gui

    h = win32gui.WindowFromPoint((x, y))
    return ctypes.windll.user32.GetAncestor(h, 2) if h else 0      # GA_ROOT


def _make_visible(c: Candidate) -> tuple[int, int]:
    """Fait défiler l'élément dans la vue et met sa fenêtre devant si elle est recouverte."""
    if c.info is not None:
        try:
            from pywinauto.controls.uiawrapper import UIAWrapper

            w = UIAWrapper(c.info)
            try:
                w.iface_scroll_item.ScrollIntoView()
                time.sleep(0.2)
            except Exception:
                pass
            r = c.info.rectangle
            sx0, sy0, sx1, sy1 = pc._screen_bounds()
            cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
            # Rectangle vide (élément disparu, menu refermé…) : on garde l'ancienne position,
            # sinon on cliquerait en (0, 0), dans le coin d'arrêt d'urgence.
            if r.width() > 1 and r.height() > 1 and sx0 <= cx < sx1 and sy0 <= cy < sy1:
                c.x, c.y = cx, cy
        except Exception:
            pass
    if c.hwnd and _owner_root(c.x, c.y) != c.hwnd:
        pc.focus_window(c.hwnd)
    return c.x, c.y


def _methods(c: Candidate, mode: str) -> list[str]:
    """Façons d'activer, de la plus sûre à la plus brute."""
    if mode == "right":
        return ["mouse_right"]
    if mode == "double":
        return ["mouse_double"]
    if c.info is None:
        return ["mouse_double", "mouse"] if mode == "open" else ["mouse"]
    if mode == "open":
        if c.kind in _LIST_KINDS:
            return ["mouse_double", "invoke"]
        if c.kind in _INVOKE_KINDS:
            return ["invoke", "mouse", "mouse_double"]
        return ["mouse_double", "invoke", "mouse"]
    # click
    if c.kind in _INVOKE_KINDS:
        return ["invoke", "mouse"]
    if c.kind in ("TabItem", "RadioButton") or c.kind in _LIST_KINDS:
        return ["mouse", "select"]
    if c.kind == "CheckBox":
        return ["toggle", "mouse"]
    return ["mouse", "invoke"]


def _apply(c: Candidate, method: str) -> None:
    if method.startswith("mouse"):
        x, y = _make_visible(c)
        if pc.emergency_stop_requested():
            raise RuntimeError("arrêt d'urgence")
        sx0, sy0, _, _ = pc._screen_bounds()
        if x <= sx0 + 4 and y <= sy0 + 4:
            raise RuntimeError("position invalide (coin d'arrêt d'urgence)")
        if method == "mouse_right":
            pc.click(x, y, button="right")
        else:
            pc.click(x, y, double=(method == "mouse_double"))
        return
    from pywinauto.controls.uiawrapper import UIAWrapper

    w = UIAWrapper(c.info)
    if method == "invoke":
        w.invoke()
    elif method == "select":
        w.select()
    elif method == "toggle":
        w.toggle()


_METHOD_LABEL = {
    "invoke": "activation directe", "select": "sélection", "toggle": "case cochée/décochée",
    "mouse": "clic", "mouse_double": "double-clic", "mouse_right": "clic droit",
}


def activate(c: Candidate, mode: str, settle_s: float = 1.0) -> Outcome:
    last_err = ""
    for method in _methods(c, mode):
        try:
            before = screen_signature()
            _apply(c, method)
            time.sleep(settle_s)
            if screen_changed(before, screen_signature()):
                return Outcome(True, f"{_METHOD_LABEL[method].capitalize()} sur « {c.name} ».", c, method)
        except Exception as exc:
            last_err = str(exc)
            logger.debug("activate %s via %s failed: %s", c.name, method, exc)
            if "arrêt d'urgence" in last_err:
                return Outcome(False, "⛔ Arrêt d'urgence demandé.", c, method)
    detail = f" ({last_err})" if last_err else ""
    return Outcome(False, f"J'ai essayé d'activer « {c.name} » mais l'écran n'a pas réagi{detail}.", c)


# ── Nom mal entendu (dictée vocale) : deviner parmi ce qui est visible ───────

def visible_names(preferred_hwnd: int | None = None, max_n: int = 90,
                  only_preferred: bool = False, items_only: bool = False) -> list[str]:
    """Noms visibles : fenêtre désignée / de travail d'abord, puis les suivantes.
    Complété par l'OCR de la fenêtre de travail si son arbre UIA est pauvre."""
    import win32gui

    names: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        s = re.sub(r"\s+", " ", s or "").strip()
        if 2 <= len(s) <= 60 and s.lower() not in seen:
            seen.add(s.lower())
            names.append(s)

    wins = search_windows(preferred_hwnd)[:1 if only_preferred else 3]
    for i, h in enumerate(wins):
        try:
            els = pc._uia_elements(h)
        except Exception:
            els = []
        for e in els:
            if (e.kind in _LIST_KINDS) if items_only else (e.kind in pc._INTERACTIVE):
                add(e.name)
        if i == 0 and len(els) < 5:            # appli opaque pour UIA : on lit l'écran
            try:
                img, (ox, oy) = pc.grab(h)
                import tempfile
                from pathlib import Path
                tmp = Path(tempfile.gettempdir()) / f"seraphim_names_{int(time.time() * 1000)}.png"
                img.save(tmp)
                try:
                    for text, _, _ in pc.ocr_lines(str(tmp)):
                        add(text)
                finally:
                    tmp.unlink(missing_ok=True)
            except Exception as exc:
                logger.debug("visible_names OCR failed: %s", exc)
        if not items_only:
            with contextlib.suppress(Exception):
                add(win32gui.GetWindowText(h))
        if len(names) >= max_n:
            break
    return names[:max_n]


_SPOKEN_EXT_RE = re.compile(
    r"\.(?:md|txt|toml|ya?ml|py|js|ts|json|lock|spec|cmd|bat|exe|pdf|docx?|xlsx?|pptx?|png|jpe?g|gif|zip)\b")
_SOUND_RULES = [
    ("eau", "o"), ("ph", "f"), ("th", "t"), ("ck", "k"), ("qu", "k"), ("ea", "i"), ("ee", "i"),
    ("ie", "i"), ("ou", "u"), ("oo", "u"), ("au", "o"), ("ai", "e"), ("ei", "e"), ("y", "i"),
    ("c(?=[ei])", "s"), ("c", "k"), ("q", "k"), ("x", "ks"), ("z", "s"), ("w", "v"), ("h", ""),
]


def sound_key(s: str) -> str:
    """Clé phonétique approximative (français + mots anglais courants) : deux noms
    qui se prononcent pareil ont des clés proches (« rythmique » ≈ « README »)."""
    s = unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()
    s = _SPOKEN_EXT_RE.sub("", s)                       # l'extension est rarement prononcée
    s = re.sub(r"[^a-z0-9]+", "", s)
    for a, b in _SOUND_RULES:
        s = re.sub(a, b, s)
    s = re.sub(r"(.)\1+", r"\1", s)                    # lettres doublées
    return re.sub(r"(?<=.)e(?=s?$)", "", s)             # e muet final


def sound_score(heard: str, name: str) -> float:
    from rapidfuzz import fuzz

    a, b = sound_key(heard), sound_key(name)
    if not a or not b:
        return 0.0
    score = float(fuzz.ratio(a, b))
    if min(len(a), len(b)) >= 0.6 * max(len(a), len(b)):   # partiel seulement à longueur comparable
        score = max(score, fuzz.partial_ratio(a, b) - 10)
    return score


def guess_misheard(heard: str, names: list[str], min_score: float = 60, margin: float = 10) -> str:
    """Nom visible qui SE PRONONCE comme ce que la dictée a compris ; "" si aucun
    ne se détache nettement (on préfère ne rien proposer que proposer n'importe quoi)."""
    scored = sorted(((sound_score(heard, n), n) for n in names), reverse=True)
    if not scored or scored[0][0] < min_score:
        return ""
    if len(scored) > 1 and scored[0][0] - scored[1][0] < margin:
        return ""
    return scored[0][1]


# ── Point d'entrée ───────────────────────────────────────────────────────────

def _dedupe(cands: list[Candidate]) -> list[Candidate]:
    out: list[Candidate] = []
    for c in cands:
        if all(abs(c.x - o.x) > 8 or abs(c.y - o.y) > 8 for o in out):
            out.append(c)
    return out


async def find(target: str, preferred_hwnd: int | None = None,
               vision_model: str = "", base_url: str = "") -> list[Candidate]:
    """Candidats triés pour `target`, en n'allant chercher plus loin que si besoin."""
    import asyncio

    needle = clean_target(target)
    _, hint = split_location(target)
    hinted = 0           # fenêtre désignée par l'utilisateur : on ne cherche QUE dedans
    if hint and not preferred_hwnd:
        hinted = await asyncio.to_thread(window_for_hint, hint) or 0
        preferred_hwnd = hinted or None
    work, _, _ = await asyncio.to_thread(pc.pick_target_window, preferred_hwnd)
    cands = await asyncio.to_thread(uia_search, needle, preferred_hwnd)
    # Rien de net dans la fenêtre de travail via UIA (appli Java, canvas, jeu…) :
    # on lit tout l'écran, et ce qui est dans la fenêtre de travail passe devant.
    if not any(c.hwnd == work and c.score >= 90 for c in cands):
        for c in await asyncio.to_thread(ocr_search, needle):
            c.hwnd = await asyncio.to_thread(_owner_root, c.x, c.y)
            if work and c.hwnd == work:
                c.score += 8
            cands.append(c)
    if hinted:                          # la fenêtre désignée, ou un menu ouvert par-dessus
        allowed = {hinted, *await asyncio.to_thread(open_popups)}
        cands = [c for c in cands if c.hwnd in allowed]
    if not cands and vision_model:
        try:
            for c in await vision_search(needle, vision_model, base_url):
                c.hwnd = await asyncio.to_thread(_owner_root, c.x, c.y)
                if not hinted or c.hwnd == hinted:
                    cands.append(c)
        except Exception as exc:
            logger.debug("vision_search failed: %s", exc)
    cands.sort(key=lambda c: -c.score)
    return _dedupe(cands)


async def locate_and_activate(target: str, mode: str = "click", preferred_hwnd: int | None = None,
                              vision_model: str = "", base_url: str = "",
                              max_candidates: int = 3, forbid=None) -> Outcome:
    """Trouve `target` n'importe où à l'écran et l'active ; essaie plusieurs
    candidats et plusieurs méthodes jusqu'à ce que l'écran réagisse.
    `forbid(nom) -> raison | None` écarte les candidats sensibles."""
    import asyncio

    if mode not in MODES:
        mode = "click"
    cands = await find(target, preferred_hwnd, vision_model, base_url)
    if not cands:
        return Outcome(False, f"Je ne trouve pas « {clean_target(target)} » à l'écran.")
    last: Outcome | None = None
    for c in cands[:max_candidates]:
        reason = forbid(c.name) if forbid else None
        if reason:
            last = Outcome(False, f"⛔ Je n'ai pas activé « {c.name} » (action sensible : {reason}). "
                                  "Si c'est bien ce que tu veux, demande-le explicitement.", c)
            continue
        last = await asyncio.to_thread(activate, c, mode)
        if last.success or last.message.startswith("⛔"):
            return last
    return last or Outcome(False, f"Je ne trouve pas « {clean_target(target)} » à l'écran.")

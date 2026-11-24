"""Lieux Windows connus : « paramètres d'affichage », « Ce PC », « gestionnaire des
tâches »… → la cible à lancer (page ms-settings:, dossier shell:, programme).

Sert quand « ouvre X » ne trouve X ni à l'écran ni comme application : plutôt que
de compter sur la mémoire du petit modèle, le code sait où se trouvent les
réglages et dossiers usuels de Windows 11.
"""

from __future__ import annotations

import re

from seraphim.desktop.activate import normalize

# (mots-clés…) → cible. Mots-clés en minuscules, sans accents (voir normalize()).
_PLACES: list[tuple[tuple[str, ...], str]] = [
    # Paramètres (« paramètres » seul : voir la fin de resolve_place)
    (("affichage", "ecran", "resolution", "luminosite", "display"), "ms-settings:display"),
    (("son", "audio", "volume", "haut-parleur", "micro", "microphone", "sound"), "ms-settings:sound"),
    (("mode sombre", "theme sombre", "couleurs", "mode clair", "dark mode", "colors"), "ms-settings:colors"),
    (("fond d ecran", "arriere-plan", "wallpaper", "background"), "ms-settings:personalization-background"),
    (("personnalisation", "personalization"), "ms-settings:personalization"),
    (("wifi", "wi-fi", "sans fil"), "ms-settings:network-wifi"),
    (("reseau", "internet", "ethernet", "network"), "ms-settings:network"),
    (("bluetooth",), "ms-settings:bluetooth"),
    (("imprimante", "printer"), "ms-settings:printers"),
    (("souris", "mouse"), "ms-settings:mousetouchpad"),
    (("pave tactile", "touchpad"), "ms-settings:devices-touchpad"),
    (("clavier", "keyboard"), "ms-settings:typing"),
    (("applications installees", "applications", "programmes installes", "apps"), "ms-settings:appsfeatures"),
    (("applications par defaut", "default apps"), "ms-settings:defaultapps"),
    (("demarrage", "startup"), "ms-settings:startupapps"),
    (("stockage", "espace disque", "storage"), "ms-settings:storagesense"),
    (("mise a jour", "mises a jour", "windows update", "update"), "ms-settings:windowsupdate"),
    (("batterie", "alimentation", "energie", "battery", "power"), "ms-settings:powersleep"),
    (("notifications",), "ms-settings:notifications"),
    (("date", "heure", "fuseau horaire", "time"), "ms-settings:dateandtime"),
    (("langue", "language"), "ms-settings:regionlanguage"),
    (("comptes", "compte", "accounts"), "ms-settings:yourinfo"),
    (("confidentialite", "privacy"), "ms-settings:privacy"),
    (("informations systeme", "a propos", "about", "specifications"), "ms-settings:about"),
    (("multitache", "multitasking"), "ms-settings:multitasking"),
    (("accessibilite", "accessibility"), "ms-settings:easeofaccess"),
    # Dossiers
    (("ce pc", "poste de travail", "disques", "lecteurs", "this pc", "my computer"), "shell:MyComputerFolder"),
    (("telechargements", "downloads"), "shell:Downloads"),
    (("documents", "mes documents"), "shell:Personal"),
    (("bureau", "desktop"), "shell:Desktop"),
    (("images", "photos", "pictures"), "shell:My Pictures"),
    (("musique", "music"), "shell:My Music"),
    (("videos",), "shell:My Video"),
    (("corbeille", "recycle bin"), "shell:RecycleBinFolder"),
    (("explorateur", "explorateur de fichiers", "explorer", "fichiers"), "explorer"),
    # Outils
    (("gestionnaire des taches", "task manager", "processus"), "taskmgr"),
    (("panneau de configuration", "control panel"), "control"),
    (("gestionnaire de peripheriques", "peripheriques", "device manager"), "devmgmt.msc"),
    (("gestion des disques", "disk management", "partitions"), "diskmgmt.msc"),
    (("version de windows", "winver"), "winver"),
    (("informations systeme detaillees", "msinfo"), "msinfo32"),
    (("services",), "services.msc"),
    (("observateur d evenements", "event viewer", "journaux"), "eventvwr.msc"),
    (("moniteur de ressources", "resource monitor"), "resmon"),
    (("invite de commandes", "terminal", "cmd"), "wt"),
    (("bloc-notes", "bloc notes", "notepad"), "notepad"),
    (("calculatrice", "calculator"), "calc"),
    (("outil capture", "capture d ecran", "snipping"), "ms-screenclip:"),
]

_FILLER_RE = re.compile(
    r"\b(?:les?|la|l|des?|du|d|mes?|ma|mon|tes?|ta|ton|pour|sur|dans|windows|de)\b", re.I)


def resolve_place(text: str) -> str | None:
    """« les paramètres d'affichage » → « ms-settings:display ». Le mot-clé le plus
    long (le plus précis) gagne ; None si rien ne correspond."""
    n = normalize(text)
    if not n:
        return None
    best: tuple[int, str] | None = None
    for keys, target in _PLACES:
        for k in keys:
            if re.search(rf"(?<!\w){re.escape(k)}(?!\w)", n) and (best is None or len(k) > best[0]):
                best = (len(k), target)
    if best:
        return best[1]
    # « paramètres » seul (ou presque) → la page d'accueil des paramètres
    rest = _FILLER_RE.sub(" ", n).strip()
    return "ms-settings:" if rest in ("parametres", "reglages") else None

"""
PC diagnostic skills — system info, processes, disks, network, installed apps.
All queries use native PowerShell/WMI: zero extra Python dependencies.
"""

from __future__ import annotations

import subprocess
from seraphim.skills.base import BaseSkill, SkillResult


def _ps(script: str, timeout: int = 15) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        return r.returncode == 0, (r.stdout or r.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return False, f"Timeout after {timeout}s"
    except Exception as e:
        return False, str(e)


# ── System Info ───────────────────────────────────────────────────────────────

class SystemInfoSkill(BaseSkill):
    name = "system_info"
    description = (
        "Retourne un snapshot complet du système : CPU (nom, usage, cœurs, fréquence), "
        "RAM (total/utilisé/libre), GPU, batterie, version Windows, uptime, "
        "nom de machine et modèle PC. "
        "Utilise pour : 'infos PC', 'état du système', 'usage CPU', 'RAM dispo', "
        "'version Windows', 'combien de RAM', 'quel CPU', 'quelle GPU', 'batterie'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "enum": ["all", "cpu", "ram", "gpu", "battery", "os"],
                "description": "Section à récupérer (default: all)",
                "default": "all",
            }
        },
        "required": [],
    }

    _SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$out = [ordered]@{}

# OS + uptime
$os  = Get-CimInstance Win32_OperatingSystem
$cs  = Get-CimInstance Win32_ComputerSystem
$uptime = (Get-Date) - $os.LastBootUpTime
$out['os'] = @{
    name    = $os.Caption
    version = $os.Version
    build   = $os.BuildNumber
    arch    = $os.OSArchitecture
    uptime  = "$([int]$uptime.TotalHours)h $($uptime.Minutes)m"
    host    = $env:COMPUTERNAME
    user    = $env:USERNAME
    model   = "$($cs.Manufacturer) $($cs.Model)"
}

# CPU
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$load = (Get-CimInstance Win32_Processor).LoadPercentage
$out['cpu'] = @{
    name    = $cpu.Name.Trim()
    cores   = $cpu.NumberOfCores
    threads = $cpu.NumberOfLogicalProcessors
    freq_mhz = $cpu.CurrentClockSpeed
    max_mhz  = $cpu.MaxClockSpeed
    usage_pct = ($load | Measure-Object -Average).Average
}

# RAM
$totalMB = [math]::Round($os.TotalVisibleMemorySize / 1024)
$freeMB  = [math]::Round($os.FreePhysicalMemory  / 1024)
$usedMB  = $totalMB - $freeMB
$out['ram'] = @{
    total_mb = $totalMB
    used_mb  = $usedMB
    free_mb  = $freeMB
    usage_pct = [math]::Round($usedMB / $totalMB * 100)
}

# GPU(s)
$gpus = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -notlike '*Microsoft*Basic*' }
$out['gpu'] = @($gpus | ForEach-Object {
    @{
        name        = $_.Name
        vram_mb     = [math]::Round($_.AdapterRAM / 1MB)
        resolution  = "$($_.CurrentHorizontalResolution)x$($_.CurrentVerticalResolution)"
        refresh_hz  = $_.CurrentRefreshRate
        driver      = $_.DriverVersion
    }
})

# Battery
$bat = Get-CimInstance Win32_Battery | Select-Object -First 1
if ($bat) {
    $status = switch ($bat.BatteryStatus) {
        1 { 'discharging' } 2 { 'AC (charging)' } 3 { 'fully charged' }
        4 { 'low' } 5 { 'critical' } 6 { 'charging' } 7 { 'charging+high' }
        8 { 'charging+low' } 9 { 'charging+critical' } default { 'unknown' }
    }
    $out['battery'] = @{
        level_pct = $bat.EstimatedChargeRemaining
        status    = $status
        minutes_remaining = $bat.EstimatedRunTime
    }
} else {
    $out['battery'] = $null
}

$out | ConvertTo-Json -Depth 4
"""

    async def run(self, section: str = "all", **kwargs) -> SkillResult:
        ok, raw = _ps(self._SCRIPT, timeout=20)
        if not ok:
            return SkillResult(success=False, output="", error=raw)

        import json
        try:
            data = json.loads(raw)
        except Exception:
            return SkillResult(success=True, output=raw)

        if section != "all" and section in data:
            data = {section: data[section]}

        lines = []
        os_d = data.get("os", {})
        if os_d:
            lines += [
                f"💻 {os_d.get('model', 'PC')} — {os_d.get('host')} ({os_d.get('user')})",
                f"🪟 {os_d.get('name')} {os_d.get('arch')} — Build {os_d.get('build')}",
                f"⏱ Uptime : {os_d.get('uptime')}",
            ]
        cpu_d = data.get("cpu", {})
        if cpu_d:
            lines += [
                f"\n🔲 CPU : {cpu_d.get('name')}",
                f"   {cpu_d.get('cores')} cœurs / {cpu_d.get('threads')} threads — "
                f"{cpu_d.get('freq_mhz')} MHz (max {cpu_d.get('max_mhz')} MHz)",
                f"   Charge : {cpu_d.get('usage_pct', 0):.0f}%",
            ]
        ram_d = data.get("ram", {})
        if ram_d:
            lines += [
                f"\n🧠 RAM : {ram_d.get('used_mb')} MB / {ram_d.get('total_mb')} MB "
                f"({ram_d.get('usage_pct')}% utilisé) — {ram_d.get('free_mb')} MB libre",
            ]
        gpus = data.get("gpu", [])
        if isinstance(gpus, dict):
            gpus = [gpus]
        for g in (gpus or []):
            vram = g.get("vram_mb", 0)
            vram_str = f"{vram} MB" if vram and vram < 100000 else "N/A"
            lines.append(
                f"\n🎮 GPU : {g.get('name')} — VRAM {vram_str} — "
                f"{g.get('resolution')} @ {g.get('refresh_hz')} Hz"
            )
        bat_d = data.get("battery")
        if bat_d:
            rem = bat_d.get("minutes_remaining", 0)
            rem_str = f" (~{rem} min)" if rem and rem < 65535 else ""
            lines.append(f"\n🔋 Batterie : {bat_d.get('level_pct')}% — {bat_d.get('status')}{rem_str}")
        else:
            lines.append("\n🔌 Alimentation : secteur (pas de batterie détectée)")

        return SkillResult(success=True, output="\n".join(lines), data=data)


# ── Process List ──────────────────────────────────────────────────────────────

class ProcessListSkill(BaseSkill):
    name = "process_list"
    description = (
        "Liste les processus en cours d'exécution avec leur PID, CPU et RAM. "
        "Utilise pour : 'quels programmes tournent', 'processus actifs', "
        "'top CPU', 'top RAM', 'est-ce que X tourne', 'usage mémoire par processus'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "sort_by": {
                "type": "string",
                "enum": ["cpu", "ram", "name"],
                "description": "Tri : cpu, ram ou name (default: cpu)",
                "default": "cpu",
            },
            "limit": {
                "type": "integer",
                "description": "Nombre max de processus à retourner (default: 20)",
                "default": 20,
            },
            "filter": {
                "type": "string",
                "description": "Filtrer par nom (ex: chrome, python). Vide = tous.",
                "default": "",
            },
        },
        "required": [],
    }

    async def run(self, sort_by: str = "cpu", limit: int = 20, filter: str = "", **kwargs) -> SkillResult:
        sort_prop = {"cpu": "CPU", "ram": "WorkingSet", "name": "ProcessName"}.get(sort_by, "CPU")
        filter_clause = f"| Where-Object {{ $_.ProcessName -like '*{filter}*' }}" if filter else ""

        script = f"""
Get-Process {filter_clause} |
  Sort-Object {sort_prop} -Descending |
  Select-Object -First {limit} |
  ForEach-Object {{
    [ordered]@{{
      name = $_.ProcessName
      pid  = $_.Id
      cpu  = [math]::Round($_.CPU, 1)
      ram_mb = [math]::Round($_.WorkingSet / 1MB, 1)
    }}
  }} | ConvertTo-Json -Depth 2
"""
        ok, raw = _ps(script)
        if not ok:
            return SkillResult(success=False, output="", error=raw)

        import json
        try:
            procs = json.loads(raw)
            if isinstance(procs, dict):
                procs = [procs]
        except Exception:
            return SkillResult(success=True, output=raw)

        header = f"{'NOM':<30} {'PID':>6}  {'CPU(s)':>8}  {'RAM MB':>8}"
        sep    = "-" * 60
        rows   = [
            f"{p.get('name',''):<30} {p.get('pid',0):>6}  {p.get('cpu',0):>8}  {p.get('ram_mb',0):>8}"
            for p in procs
        ]
        title = f"Processus ({sort_by.upper()}) — top {limit}"
        if filter:
            title += f" filtrés sur '{filter}'"
        output = "\n".join([title, header, sep] + rows)
        return SkillResult(success=True, output=output, data={"processes": procs})


# ── Process Kill ──────────────────────────────────────────────────────────────

class ProcessKillSkill(BaseSkill):
    name = "process_kill"
    description = (
        "Arrête un processus Windows par son nom ou son PID. "
        "Utilise pour : 'ferme X', 'tue le processus Y', 'kill PID 1234', "
        "'arrête chrome', 'force quit'. "
        "ATTENTION : demande confirmation avant de tuer des processus système."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Nom du processus (ex: chrome, notepad). Sans .exe.",
                "default": "",
            },
            "pid": {
                "type": "integer",
                "description": "PID du processus à arrêter.",
                "default": 0,
            },
            "force": {
                "type": "boolean",
                "description": "Force l'arrêt même si le processus résiste (default: false)",
                "default": False,
            },
        },
        "required": [],
    }

    async def run(self, name: str = "", pid: int = 0, force: bool = False, **kwargs) -> SkillResult:
        if not name and not pid:
            return SkillResult(success=False, output="", error="Fournir 'name' ou 'pid'.")

        if pid:
            target = f"-Id {pid}"
            label  = f"PID {pid}"
        else:
            target = f"-Name '{name}'"
            label  = name

        force_flag = "-Force" if force else ""
        script = f"Stop-Process {target} {force_flag} -ErrorAction Stop"
        ok, out = _ps(script)
        if not ok:
            return SkillResult(success=False, output="", error=f"Impossible d'arrêter {label} : {out}")
        return SkillResult(success=True, output=f"✓ Processus {label} arrêté.")


# ── Disk Info ─────────────────────────────────────────────────────────────────

class DiskInfoSkill(BaseSkill):
    name = "disk_info"
    description = (
        "Affiche l'espace disque de tous les lecteurs (C:, D:, etc.) avec total/utilisé/libre. "
        "Peut aussi calculer la taille d'un dossier spécifique. "
        "Utilise pour : 'espace disque', 'disque plein', 'combien de place', "
        "'taille de C:', 'analyse le dossier X', 'combien prend le bureau'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "folder": {
                "type": "string",
                "description": "Dossier dont calculer la taille (optionnel). Ex: C:\\Users\\ostap\\Downloads",
                "default": "",
            }
        },
        "required": [],
    }

    async def run(self, folder: str = "", **kwargs) -> SkillResult:
        # All drives
        drive_script = r"""
Get-PSDrive -PSProvider FileSystem |
  Where-Object { $_.Used -ne $null } |
  ForEach-Object {
    $total = $_.Used + $_.Free
    [ordered]@{
      drive    = $_.Name
      total_gb = [math]::Round($total / 1GB, 1)
      used_gb  = [math]::Round($_.Used  / 1GB, 1)
      free_gb  = [math]::Round($_.Free  / 1GB, 1)
      usage_pct = if ($total -gt 0) { [math]::Round($_.Used / $total * 100) } else { 0 }
    }
  } | ConvertTo-Json -Depth 2
"""
        ok, raw = _ps(drive_script)
        lines = ["💾 Disques :"]
        if ok:
            import json
            try:
                drives = json.loads(raw)
                if isinstance(drives, dict):
                    drives = [drives]
                for d in drives:
                    bar_filled = int(d.get('usage_pct', 0) / 10)
                    bar = "█" * bar_filled + "░" * (10 - bar_filled)
                    lines.append(
                        f"  {d['drive']:>2}: [{bar}] {d['usage_pct']:>3}%  "
                        f"{d['used_gb']} / {d['total_gb']} GB  ({d['free_gb']} GB libre)"
                    )
            except Exception:
                lines.append(raw)
        else:
            lines.append(f"  Erreur : {raw}")

        if folder:
            folder_esc = folder.replace("'", "''")
            size_script = f"""
$size = (Get-ChildItem -Path '{folder_esc}' -Recurse -ErrorAction SilentlyContinue |
         Measure-Object -Property Length -Sum).Sum
[math]::Round($size / 1MB, 1)
"""
            ok2, size_raw = _ps(size_script, timeout=30)
            if ok2 and size_raw:
                try:
                    mb = float(size_raw)
                    gb = mb / 1024
                    if gb >= 1:
                        size_str = f"{gb:.2f} GB"
                    else:
                        size_str = f"{mb:.1f} MB"
                    lines.append(f"\n📁 {folder} : {size_str}")
                except Exception:
                    lines.append(f"\n📁 {folder} : {size_raw}")
            else:
                lines.append(f"\n📁 {folder} : erreur — {size_raw}")

        return SkillResult(success=True, output="\n".join(lines))


# ── Network Info ──────────────────────────────────────────────────────────────

class NetworkInfoSkill(BaseSkill):
    name = "network_info"
    description = (
        "Retourne les informations réseau : IP locale, IP publique, WiFi (SSID, signal), "
        "adaptateurs actifs, et peut faire un ping. "
        "Utilise pour : 'mon IP', 'réseau WiFi', 'quelle connexion', 'ping X', "
        "'internet connecté', 'vitesse réseau', 'adresse IP publique'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "ping": {
                "type": "string",
                "description": "Hôte à pinguer (ex: google.com, 8.8.8.8). Vide = pas de ping.",
                "default": "",
            }
        },
        "required": [],
    }

    async def run(self, ping: str = "", **kwargs) -> SkillResult:
        script = r"""
$out = [ordered]@{}

# Adaptateurs actifs
$adapters = Get-NetAdapter | Where-Object { $_.Status -eq 'Up' }
$out['adapters'] = @($adapters | ForEach-Object {
    $ip = (Get-NetIPAddress -InterfaceIndex $_.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
           Select-Object -First 1).IPAddress
    @{ name=$_.Name; type=$_.MediaType; speed_mbps=[math]::Round($_.LinkSpeed/1MB); ip=$ip; mac=$_.MacAddress }
})

# WiFi
$wifi = netsh wlan show interfaces 2>$null
$ssid    = ($wifi | Select-String 'SSID\s*:' | Select-Object -First 1) -replace '.*SSID\s*:\s*',''
$signal  = ($wifi | Select-String 'Signal\s*:') -replace '.*Signal\s*:\s*',''
$bssid   = ($wifi | Select-String 'BSSID\s*:') -replace '.*BSSID\s*:\s*',''
if ($ssid) {
    $out['wifi'] = @{ ssid=$ssid.Trim(); signal=$signal.Trim(); bssid=$bssid.Trim() }
} else {
    $out['wifi'] = $null
}

$out | ConvertTo-Json -Depth 3
"""
        ok, raw = _ps(script, timeout=15)
        import json
        lines = []

        if ok:
            try:
                data = json.loads(raw)
                adapters = data.get("adapters", [])
                if isinstance(adapters, dict):
                    adapters = [adapters]
                if adapters:
                    lines.append("🌐 Adaptateurs actifs :")
                    for a in adapters:
                        lines.append(
                            f"  {a.get('name')} ({a.get('type', '?')}) — "
                            f"IP {a.get('ip', 'N/A')} — {a.get('speed_mbps', '?')} Mbps"
                        )
                wifi = data.get("wifi")
                if wifi:
                    lines.append(f"\n📶 WiFi : {wifi.get('ssid')} — Signal {wifi.get('signal')}")
                else:
                    lines.append("\n📡 WiFi : non connecté")
            except Exception:
                lines.append(raw)
        else:
            lines.append(f"Erreur adaptateurs : {raw}")

        # IP publique
        ip_script = r"""
try {
    $r = Invoke-WebRequest -Uri 'https://api.ipify.org' -UseBasicParsing -TimeoutSec 5
    $r.Content.Trim()
} catch { 'N/A' }
"""
        ok2, pub_ip = _ps(ip_script, timeout=10)
        lines.append(f"\n🌍 IP publique : {pub_ip.strip() if ok2 else 'N/A'}")

        # Ping
        if ping:
            ping_esc = ping.replace("'", "''").replace(";", "").replace("&", "")
            ping_script = f"""
$r = Test-Connection -ComputerName '{ping_esc}' -Count 4 -ErrorAction SilentlyContinue
if ($r) {{
    $avg = [math]::Round(($r | Measure-Object -Property ResponseTime -Average).Average)
    $min = ($r | Measure-Object -Property ResponseTime -Minimum).Minimum
    $max = ($r | Measure-Object -Property ResponseTime -Maximum).Maximum
    "✅ $avg ms avg (min $min / max $max ms)"
}} else {{ "❌ Hôte inaccessible" }}
"""
            ok3, ping_out = _ps(ping_script, timeout=20)
            lines.append(f"\n📡 Ping {ping} : {ping_out.strip() if ok3 else 'erreur'}")

        return SkillResult(success=True, output="\n".join(lines))


# ── Installed Apps — helpers ──────────────────────────────────────────────────

import json as _json
import re as _re
from pathlib import Path as _Path

# Config de catégories personnalisées, spécifique à chaque machine
_CATS_CONFIG: _Path = _Path.home() / ".seraphim" / "app_categories.json"
_custom_cache: dict | None = None


def _load_custom() -> dict[str, list[str]]:
    global _custom_cache
    if _custom_cache is None:
        try:
            if _CATS_CONFIG.exists():
                _custom_cache = _json.loads(_CATS_CONFIG.read_text(encoding="utf-8")).get("custom", {})
            else:
                _custom_cache = {}
        except Exception:
            _custom_cache = {}
    return _custom_cache


def _save_custom(custom: dict[str, list[str]]) -> None:
    global _custom_cache
    try:
        _CATS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        _CATS_CONFIG.write_text(_json.dumps({"version": 1, "custom": custom}, ensure_ascii=False, indent=2), encoding="utf-8")
        _custom_cache = custom
    except Exception:
        pass

# (emoji, label, name_substrings, publisher_substrings)
_APP_CATS = [
    ("🎮", "Jeux", [
        "call of duty", "need for speed", "elden ring", "hades", "hollow knight",
        "darkest dungeon", "dead by daylight", "lethal company", "brawlhalla",
        "metro 2033", "dying light", "garry's mod", "death stranding", "a plague tale",
        "ark: survival", "jurassic", "among us", "blasphemous", "into the dead",
        "burglin", "2xko", "league of legends", "legends of runeterra", "valorant",
        "overwatch", "diablo", "hearthstone", "minecraft", "silksong",
    ], [
        "riot games", "studio wildcard", "team cherry", "behaviour interactive",
        "zeekerss", "blue mammoth games", "gog.com", "game kitchen", "innersloth",
        "facepunch studios", "kojima productions", "fobri", "frontier developments",
        "team peak", "r.g. mechanics", "torrent-igruha.org",
    ]),
    ("💻", "Développement", [
        "android studio", "visual studio code", "intellij idea", "clion", "phpstorm",
        "datagrip", "webstorm", "goland", "node.js", "git ", "mysql workbench",
        "mysql server", "mysql router", "mysql shell", "mysql installer",
        "mysql documents", "mysql examples", "postman", "gns3", "cisco packet tracer",
        "virtualbox", "eclipse temurin", "miktex", "npcap", "notepad++",
        "powershell 7", "composer - php", "wireshark", "putty", "winscp",
        "python 3", "python 3.", "python launcher",
    ], [
        "jetbrains s.r.o.", "oracle corporation", "git development community",
        "node.js foundation", "nmap project", "gns3 technology", "cisco systems",
        "notepad++ team", "oracle and/or its affiliates", "getcomposer.org",
        "python software foundation",
    ]),
    ("🎨", "Créatif & Médias", [
        "adobe photoshop", "adobe acrobat", "adobe premiere", "adobe after effects",
        "obs studio", "handbrake", "capcut", "cinema 4d", "magic bullet", "ffmpeg",
        "audacity", "blender", "davinci resolve",
    ], [
        "obs project", "maxon computer gmbh", "gyan", "bytedance pte. ltd.",
    ]),
    ("🌐", "Internet & Réseau", [
        "google chrome", "mozilla firefox", "discord", "cloudflare warp", "expressvpn",
        "comet", "blitz", "microsoft teams meeting",
    ], [
        "mozilla", "discord inc.", "cloudflare, inc.", "expressvpn",
    ]),
    ("📝", "Productivité", [
        "microsoft 365", "libreoffice", "obsidian", "onenote",
        "docs 1.0", "gmail 1.0", "feuilles de calcul", "google drive 1.0",
        "google•drive", "microsoft onedrive",
    ], [
        "the document foundation", "obsidian",
    ]),
    ("🚀", "Launchers & Plateformes", [
        "epic games launcher", "battle.net", "overwolf", "porofessor",
        "hoyoplay", "ankama launcher", "blitz 2", "steam",
        "launcher prerequisites", "epic online services",
    ], [
        "blizzard entertainment", "overwolf ltd.", "cognosphere pte. ltd.", "ankama",
        "balena inc.", "blitz, inc.",
    ]),
]

# Patterns de condensation (label affiché, regex sur le nom)
_COLLAPSE = [
    ("Microsoft Visual C++ Redistributables",
     _re.compile(r"microsoft visual c\+\+", _re.I)),
    ("Microsoft .NET Runtimes",
     _re.compile(r"microsoft \.net (host|runtime|windows desktop)", _re.I)),
    ("NVIDIA Services & Containers",
     _re.compile(r"nvidia (container|backend|session container|localsystem container|"
                 r"user container|aiuser container|messagebus|watchdog|telemetry|"
                 r"install application|nvcpl|nvdlisr|shadowplay|virtual audio|"
                 r"usbc driver|platform controllers|framerview)", _re.I)),
    ("Office Click-to-Run Components",
     _re.compile(r"office 16 click-to-run", _re.I)),
    ("Microsoft Visual Studio Setup",
     _re.compile(r"microsoft visual studio (setup|installer|configuration|wmi)", _re.I)),
    ("Application Verifier / Kits",
     _re.compile(r"(application verifier|kits configuration|msi development tools)", _re.I)),
]

_CAT_ORDER = [
    "🎮 Jeux", "💻 Développement", "🎨 Créatif & Médias",
    "🌐 Internet & Réseau", "📝 Productivité", "🚀 Launchers & Plateformes",
    "🔧 Système & Drivers", "📦 Autres",
]

# Aliases pour résoudre le nom de catégorie depuis le langage naturel
_CAT_ALIASES: dict[str, str] = {
    "jeux": "🎮 Jeux", "game": "🎮 Jeux", "gaming": "🎮 Jeux", "jeu": "🎮 Jeux",
    "dev": "💻 Développement", "développement": "💻 Développement",
    "development": "💻 Développement", "code": "💻 Développement",
    "programmation": "💻 Développement", "outils": "💻 Développement",
    "créatif": "🎨 Créatif & Médias", "creative": "🎨 Créatif & Médias",
    "média": "🎨 Créatif & Médias", "media": "🎨 Créatif & Médias",
    "photo": "🎨 Créatif & Médias", "vidéo": "🎨 Créatif & Médias",
    "internet": "🌐 Internet & Réseau", "réseau": "🌐 Internet & Réseau",
    "network": "🌐 Internet & Réseau", "web": "🌐 Internet & Réseau",
    "productivité": "📝 Productivité", "productivity": "📝 Productivité",
    "bureau": "📝 Productivité", "office": "📝 Productivité",
    "launcher": "🚀 Launchers & Plateformes", "plateforme": "🚀 Launchers & Plateformes",
    "platform": "🚀 Launchers & Plateformes",
    "système": "🔧 Système & Drivers", "system": "🔧 Système & Drivers",
    "driver": "🔧 Système & Drivers", "drivers": "🔧 Système & Drivers",
    "autre": "📦 Autres", "autres": "📦 Autres", "other": "📦 Autres",
}


def _resolve_category(text: str) -> str | None:
    """Résout un nom de catégorie depuis du texte libre (ex: 'les jeux', 'dev')."""
    t = text.lower().strip()
    for alias, cat in _CAT_ALIASES.items():
        if alias in t:
            return cat
    for cat in _CAT_ORDER:
        label = cat.split(" ", 1)[1].lower()
        if label in t:
            return cat
    return None


def _classify_app(name: str, publisher: str) -> str:
    n = name.lower()
    p = (publisher or "").lower()
    # 1. Catégories apprises (config PC)
    for cat, kws in _load_custom().items():
        if any(kw.lower() in n for kw in kws):
            return cat
    # 2. Règles built-in (publisher + nom)
    for emoji, label, name_kws, pub_kws in _APP_CATS:
        if any(kw in n for kw in name_kws):
            return f"{emoji} {label}"
        if any(pk in p for pk in pub_kws):
            return f"{emoji} {label}"
    # 3. Systèmes & drivers
    sys_pub = {"microsoft corporation", "intel corporation", "hp inc.", "logitech",
               "nvidia corporation", "advanced micro devices"}
    sys_name = ["nvidia ", "microsoft visual c++", "microsoft .net", "microsoft windows desktop",
                "java auto updater", "openal", "adobe refresh manager", "adobe creative cloud",
                "hp audio", "hp connection", "hp documentation", "logitech g hub",
                "microsoft gameinput", "microsoft update health", "microsoft edge webview2",
                "amd ryzen master", "nvcpl", "intel(r) c++"]
    if any(sp in n for sp in sys_name) or any(sp in p for sp in sys_pub):
        return "🔧 Système & Drivers"
    return "📦 Autres"


def _short_ver(ver: str) -> str:
    """Garde uniquement major.minor si la version est longue."""
    if not ver:
        return ""
    parts = ver.split(".")
    if len(parts) <= 2:
        return ver
    if all(p.isdigit() for p in parts):
        return f"{parts[0]}.{parts[1]}"
    return parts[0]


def _ver_tag(name: str, ver: str) -> str:
    """Retourne ` `major.minor`` ou vide si la version est redondante avec le nom."""
    sv = _short_ver(ver)
    if not sv:
        return ""
    major = sv.split(".")[0]
    # Build number interne (ex: JetBrains 252.xxxxx) quand le nom contient déjà l'année
    if major.isdigit() and int(major) > 100 and _re.search(r"\b20\d{2}\b", name):
        return ""
    # Version déjà présente dans le nom (ex: "Node.js 24.15.0")
    if sv in name.lower() or major in name.lower().split():
        return ""
    return f" `{sv}`"


def _format_app_list(apps: list, filter_str: str = "") -> str:
    if not apps:
        suffix = f" pour '{filter_str}'" if filter_str else ""
        return f"❌ Aucune application trouvée{suffix}."

    # Catégoriser
    buckets: dict[str, list] = {c: [] for c in _CAT_ORDER}
    for a in apps:
        cat = _classify_app(a.get("name", ""), a.get("publisher", ""))
        buckets.setdefault(cat, []).append(a)

    header = f"📋 **{len(apps)} applications installées**"
    if filter_str:
        header += f"  _(filtre : « {filter_str} »)_"
    lines = [header, ""]

    for cat in _CAT_ORDER:
        cat_apps = buckets.get(cat, [])
        if not cat_apps:
            continue

        # Séparer les condensables des réguliers
        collapsed: dict[str, list] = {}
        regular: list = []
        for a in cat_apps:
            n = a.get("name", "")
            matched_collapse = None
            for lbl, pat in _COLLAPSE:
                if pat.search(n):
                    matched_collapse = lbl
                    break
            if matched_collapse:
                collapsed.setdefault(matched_collapse, []).append(a)
            else:
                regular.append(a)

        lines.append(f"**{cat}**  ({len(cat_apps)})")

        # Apps normales
        for a in regular:
            name = a.get("name", "")
            suffix = _ver_tag(name, a.get("version") or "")
            lines.append(f"  • {name}{suffix}")

        # Groupes condensés
        for lbl, grp in collapsed.items():
            if len(grp) == 1:
                a = grp[0]
                name = a.get("name", "")
                suffix = _ver_tag(name, a.get("version") or "")
                lines.append(f"  • {name}{suffix}")
            else:
                vers = sorted({_short_ver(a.get("version") or "") for a in grp if a.get("version")})
                ver_hint = f" — versions : {', '.join(vers[:3])}" if vers else ""
                lines.append(f"  ▸ {lbl} _({len(grp)} entrées{ver_hint})_")

        lines.append("")

    return "\n".join(lines).rstrip()


# ── Installed Apps ────────────────────────────────────────────────────────────

class InstalledAppsSkill(BaseSkill):
    name = "installed_apps"
    description = (
        "Gère les logiciels installés sur Windows (registre Win32 + Microsoft Store). "
        "Actions : list (lister), add_to_category (apprendre une classification), "
        "remove_from_category (oublier), list_categories (voir les règles apprises). "
        "Utilise pour : 'logiciels installés', 'est-ce que X est installé', "
        "'ajoute X dans les jeux', 'il manque X', 'X est mal classé'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "add_to_category", "remove_from_category", "list_categories"],
                "description": "Action : list (défaut), add_to_category, remove_from_category, list_categories",
                "default": "list",
            },
            "filter": {
                "type": "string",
                "description": "Filtrer par nom (ex: python, spotify). Vide = tout.",
                "default": "",
            },
            "limit": {
                "type": "integer",
                "description": "Nombre max de résultats pour 'list' (default: 200).",
                "default": 200,
            },
            "include_store": {
                "type": "boolean",
                "description": "Inclure les apps Microsoft Store (default: true)",
                "default": True,
            },
            "app": {
                "type": "string",
                "description": "Nom exact (ou fragment) de l'app pour add/remove_to_category.",
                "default": "",
            },
            "category": {
                "type": "string",
                "description": "Catégorie cible. Ex: 'jeux', 'dev', 'créatif', '🎮 Jeux'.",
                "default": "",
            },
        },
        "required": [],
    }

    async def run(
        self,
        action: str = "list",
        filter: str = "",
        limit: int = 200,
        include_store: bool = True,
        app: str = "",
        category: str = "",
        **kwargs,
    ) -> SkillResult:

        # ── add_to_category ───────────────────────────────────────────────────
        if action == "add_to_category":
            target = app or filter
            if not target:
                return SkillResult(success=False, output="", error="Paramètre 'app' requis.")
            cat = _resolve_category(category) if category else None
            if not cat:
                opts = ", ".join(f"'{c}'" for c in _CAT_ORDER)
                return SkillResult(success=False, output="",
                                   error=f"Catégorie non reconnue. Options : {opts}")
            kw = target.lower().strip()
            custom = _load_custom()
            if kw not in custom.get(cat, []):
                custom.setdefault(cat, []).append(kw)
                _save_custom(custom)
            return SkillResult(
                success=True,
                output=(
                    f"✅ **{target}** sera désormais classé dans **{cat}**.\n"
                    f"Cette règle est sauvegardée sur ce PC ({_CATS_CONFIG})."
                ),
            )

        # ── remove_from_category ─────────────────────────────────────────────
        if action == "remove_from_category":
            target = (app or filter).lower().strip()
            if not target:
                return SkillResult(success=False, output="", error="Paramètre 'app' requis.")
            custom = _load_custom()
            removed = []
            for cat_name, kws in custom.items():
                if target in kws:
                    kws.remove(target)
                    removed.append(cat_name)
            if removed:
                _save_custom(custom)
                return SkillResult(success=True, output=f"✅ Règle « {target} » supprimée de : {', '.join(removed)}.")
            return SkillResult(success=True, output=f"Aucune règle personnalisée trouvée pour « {target} ».")

        # ── list_categories ───────────────────────────────────────────────────
        if action == "list_categories":
            custom = _load_custom()
            if not custom:
                return SkillResult(success=True, output="Aucune règle personnalisée. Classification auto uniquement.")
            lines = [f"**Règles apprises sur ce PC** ({_CATS_CONFIG}) :"]
            for cat_name, kws in custom.items():
                if kws:
                    lines.append(f"  **{cat_name}** : {', '.join(kws)}")
            return SkillResult(success=True, output="\n".join(lines))

        # ── list (default) ───────────────────────────────────────────────────
        import json

        # When filtering, scan everything to find all matches
        reg_limit = 2000 if filter else max(limit, 1)
        filter_clause = f"| Where-Object {{ $_.DisplayName -like '*{filter}*' }}" if filter else ""

        reg_script = f"""
$paths = @(
    'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',
    'HKLM:\\Software\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',
    'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*'
)
Get-ItemProperty $paths -ErrorAction SilentlyContinue |
  Where-Object {{ $_.DisplayName -and $_.DisplayName -ne '' }} {filter_clause} |
  Sort-Object DisplayName |
  Select-Object -First {reg_limit} |
  ForEach-Object {{
    [ordered]@{{
      name      = $_.DisplayName
      version   = $_.DisplayVersion
      publisher = $_.Publisher
      source    = 'win32'
    }}
  }} | ConvertTo-Json -Depth 2
"""
        ok, raw = _ps(reg_script, timeout=30)
        reg_apps: list = []
        if ok and raw:
            try:
                parsed = json.loads(raw)
                reg_apps = parsed if isinstance(parsed, list) else ([parsed] if parsed else [])
            except Exception:
                pass
        elif not ok:
            return SkillResult(success=False, output="", error=raw)

        store_apps: list = []
        if include_store:
            filter_store = f"| Where-Object {{ $_.Name -like '*{filter}*' }}" if filter else ""
            store_script = f"""
Get-AppxPackage -AllUsers -ErrorAction SilentlyContinue |
  Where-Object {{ $_.IsFramework -eq $false -and $_.SignatureKind -in @('Store','Developer','None') }} {filter_store} |
  Sort-Object Name |
  ForEach-Object {{
    [ordered]@{{
      name      = ($_.Name -replace '^[A-Za-z0-9]+\\.', '')
      version   = $_.Version
      publisher = $_.PublisherDisplayName
      source    = 'store'
    }}
  }} | ConvertTo-Json -Depth 2
"""
            ok2, raw2 = _ps(store_script, timeout=20)
            if ok2 and raw2:
                try:
                    parsed2 = json.loads(raw2)
                    store_apps = parsed2 if isinstance(parsed2, list) else ([parsed2] if parsed2 else [])
                except Exception:
                    pass

        # Merge — deduplicate by lowercase name
        seen: set = set()
        all_apps: list = []
        for a in reg_apps:
            n = (a.get("name") or "").strip().lower()
            if n and n not in seen:
                seen.add(n)
                all_apps.append(a)
        for a in store_apps:
            n = (a.get("name") or "").strip().lower()
            if n and n not in seen:
                seen.add(n)
                all_apps.append(a)

        if not filter and limit > 0:
            all_apps = all_apps[:limit]

        if not all_apps:
            msg = (
                f"❌ Aucune application correspondant à '{filter}' trouvée."
                if filter
                else "Aucune application trouvée."
            )
            return SkillResult(success=True, output=msg, data={"apps": []})

        output = _format_app_list(all_apps, filter_str=filter)
        return SkillResult(success=True, output=output, data={"apps": all_apps})


# ── Windows Settings ──────────────────────────────────────────────────────────

class WindowsSettingsSkill(BaseSkill):
    name = "windows_settings"
    description = (
        "Lit ou modifie les paramètres Windows : plan d'alimentation, résolution d'écran, "
        "programmes au démarrage, état du pare-feu, mises à jour disponibles. "
        "Utilise pour : 'plan énergie', 'changer résolution', 'démarrage Windows', "
        "'pare-feu actif', 'mises à jour Windows', 'économie batterie'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "set_power_plan", "set_resolution", "list_startup", "check_updates"],
                "description": "Action : read (état général), set_power_plan, set_resolution, list_startup, check_updates",
                "default": "read",
            },
            "power_plan": {
                "type": "string",
                "enum": ["balanced", "performance", "power_saver"],
                "description": "Plan d'alimentation (pour set_power_plan)",
            },
            "width": {"type": "integer", "description": "Largeur résolution (pour set_resolution)"},
            "height": {"type": "integer", "description": "Hauteur résolution (pour set_resolution)"},
        },
        "required": ["action"],
    }

    _POWER_GUIDS = {
        "balanced":     "381b4222-f694-41f0-9685-ff5bb260df2e",
        "performance":  "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
        "power_saver":  "a1841308-3541-4fab-bc81-f71556f20b4a",
    }

    async def run(
        self,
        action: str = "read",
        power_plan: str = "",
        width: int = 0,
        height: int = 0,
        **kwargs,
    ) -> SkillResult:

        if action == "read":
            script = r"""
$out = [ordered]@{}

# Plan d'alimentation actif
$plan = powercfg /getactivescheme
$out['power_plan'] = $plan -replace 'Power Scheme GUID: [a-f0-9-]+ +\((.+)\).*','$1'

# Pare-feu
$fw = Get-NetFirewallProfile | Select-Object Name, Enabled
$out['firewall'] = @($fw | ForEach-Object { @{ profile=$_.Name; enabled=$_.Enabled } })

# Heure / fuseau horaire
$tz = (Get-TimeZone).DisplayName
$out['timezone'] = $tz

# Windows Update dernier check
$wu = (New-Object -ComObject Microsoft.Update.AutoUpdate).Results
$out['last_update_check'] = if ($wu.LastSearchSuccessDate) { $wu.LastSearchSuccessDate.ToString('yyyy-MM-dd HH:mm') } else { 'N/A' }

$out | ConvertTo-Json -Depth 3
"""
            ok, raw = _ps(script, timeout=20)
            if not ok:
                return SkillResult(success=False, output="", error=raw)
            import json
            try:
                data = json.loads(raw)
                lines = [
                    f"⚡ Plan d'alimentation : {data.get('power_plan', 'N/A').strip()}",
                    f"🕐 Fuseau horaire : {data.get('timezone', 'N/A')}",
                    f"🔄 Dernier check MàJ : {data.get('last_update_check', 'N/A')}",
                ]
                for f in (data.get("firewall") or []):
                    status = "✅ actif" if f.get("enabled") else "❌ désactivé"
                    lines.append(f"🛡 Pare-feu {f.get('profile')} : {status}")
                return SkillResult(success=True, output="\n".join(lines), data=data)
            except Exception:
                return SkillResult(success=True, output=raw)

        elif action == "set_power_plan":
            guid = self._POWER_GUIDS.get(power_plan)
            if not guid:
                return SkillResult(success=False, output="", error=f"Plan inconnu : {power_plan}")
            ok, out = _ps(f"powercfg /setactive {guid}")
            label = {"balanced": "Équilibré", "performance": "Performances élevées", "power_saver": "Économiseur d'énergie"}
            return SkillResult(
                success=ok,
                output=f"✓ Plan '{label.get(power_plan)}' activé." if ok else "",
                error=out if not ok else "",
            )

        elif action == "set_resolution":
            if not width or not height:
                return SkillResult(success=False, output="", error="Fournir width et height.")
            script = f"""
Add-Type @"
using System; using System.Runtime.InteropServices;
public class Display {{
    [DllImport("user32.dll")] public static extern bool ChangeDisplaySettings(ref DEVMODE dm, int flags);
    [DllImport("user32.dll")] public static extern bool EnumDisplaySettings(string device, int mode, ref DEVMODE dm);
    [StructLayout(LayoutKind.Sequential)] public struct DEVMODE {{
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmDeviceName;
        public short dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra;
        public int dmFields; public int dmPositionX, dmPositionY; public int dmDisplayOrientation;
        public int dmDisplayFixedOutput; public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmFormName;
        public short dmLogPixels; public int dmBitsPerPel, dmPelsWidth, dmPelsHeight;
        public int dmDisplayFlags, dmDisplayFrequency, dmICMMethod, dmICMIntent, dmMediaType;
        public int dmDitherType, dmReserved1, dmReserved2, dmPanningWidth, dmPanningHeight;
    }}
}}
"@
$dm = New-Object Display+DEVMODE; $dm.dmSize = [System.Runtime.InteropServices.Marshal]::SizeOf($dm)
[Display]::EnumDisplaySettings($null, -1, [ref]$dm) | Out-Null
$dm.dmPelsWidth = {width}; $dm.dmPelsHeight = {height}; $dm.dmFields = 0x80000 -bor 0x100000
[Display]::ChangeDisplaySettings([ref]$dm, 0)
"""
            ok, out = _ps(script, timeout=10)
            return SkillResult(
                success=ok,
                output=f"✓ Résolution changée à {width}x{height}." if ok else "",
                error=out if not ok else "",
            )

        elif action == "list_startup":
            script = r"""
$items = @()
$items += Get-CimInstance Win32_StartupCommand | ForEach-Object { @{ name=$_.Name; command=$_.Command; location=$_.Location } }
$items | ConvertTo-Json -Depth 2
"""
            ok, raw = _ps(script, timeout=15)
            if not ok:
                return SkillResult(success=False, output="", error=raw)
            import json
            try:
                items = json.loads(raw)
                if isinstance(items, dict):
                    items = [items]
                lines = [f"🚀 Programmes au démarrage ({len(items)}) :"]
                for item in items:
                    lines.append(f"  • {item.get('name')} — {item.get('command', '')[:80]}")
                return SkillResult(success=True, output="\n".join(lines), data={"startup": items})
            except Exception:
                return SkillResult(success=True, output=raw)

        elif action == "check_updates":
            script = r"""
$session = New-Object -ComObject Microsoft.Update.Session
$searcher = $session.CreateUpdateSearcher()
try {
    $result = $searcher.Search("IsInstalled=0 and Type='Software'")
    $count = $result.Updates.Count
    if ($count -eq 0) {
        "✅ Windows est à jour (aucune mise à jour disponible)"
    } else {
        $names = ($result.Updates | ForEach-Object { $_.Title }) -join "`n  • "
        "⚠️ $count mise(s) à jour disponible(s) :`n  • $names"
    }
} catch {
    "Impossible de vérifier les mises à jour : $_"
}
"""
            ok, out = _ps(script, timeout=60)
            return SkillResult(success=ok, output=out, error="" if ok else out)

        return SkillResult(success=False, output="", error=f"Action inconnue : {action}")

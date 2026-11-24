# Seraphim — dev launcher
# Starts the Python backend + Tauri desktop app in one go.
# Closing the app window also stops the backend.
#
# Usage:  .\scripts\start.ps1          (or double-click Seraphim.cmd at repo root)

$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$UI   = Join-Path $Root 'seraphim-ui'

# --- 1. PATH -----------------------------------------------------------------
$extra = @(
    "$env:LOCALAPPDATA\Programs\Ollama",
    "$env:USERPROFILE\.local\bin",
    "$env:USERPROFILE\.cargo\bin",
    "C:\Program Files\nodejs"
)
foreach ($p in $extra) { if ((Test-Path $p) -and ($env:PATH -notlike "*$p*")) { $env:PATH = "$p;$env:PATH" } }

function Info($m) { Write-Host "[seraphim] $m" -ForegroundColor Cyan }

# --- 2. Ollama ---------------------------------------------------------------
if (-not (Get-Process ollama -ErrorAction SilentlyContinue)) {
    Info "Starting Ollama..."
    $ollamaApp = "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"
    if (Test-Path $ollamaApp) { Start-Process $ollamaApp } else { Start-Process ollama -ArgumentList 'serve' -WindowStyle Hidden }
}

# --- 3. Free ports from previous runs ----------------------------------------
foreach ($port in 7272, 1420) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        Info "Port $port busy (PID $($_.OwningProcess)) - stopping it"
        & taskkill /T /F /PID $_.OwningProcess 2>&1 | Out-Null
    }
}

# Tauri's bundle config globs resources/seraphim-server/** (filled by build.ps1).
# In dev it may be missing, which breaks `cargo run` — create a placeholder.
$res = Join-Path $UI 'src-tauri\resources\seraphim-server'
if (-not (Test-Path $res)) {
    New-Item -ItemType Directory -Force $res | Out-Null
    New-Item -ItemType File (Join-Path $res '.placeholder') | Out-Null
}

# --- 4. Backend (own minimized window, so logs stay visible) -----------------
Info "Starting backend..."
$backend = Start-Process uv -ArgumentList 'run', 'seraphim', 'serve' `
    -WorkingDirectory $Root -WindowStyle Minimized -PassThru

try {
    # --- 5. Wait for API -------------------------------------------------------
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        if ($backend.HasExited) { throw "Backend exited early (code $($backend.ExitCode))" }
        try { Invoke-WebRequest http://127.0.0.1:7272/docs -UseBasicParsing -TimeoutSec 2 | Out-Null; $ready = $true; break } catch { Start-Sleep 1 }
    }
    if (-not $ready) { throw "Backend did not answer on :7272 after 60s" }
    Info "Backend ready on http://localhost:7272"

    # --- 6. Desktop app (foreground) -------------------------------------------
    Info "Starting desktop app..."
    Push-Location $UI
    try { npm run tauri dev } finally { Pop-Location }
}
finally {
    Info "Stopping backend..."
    if (-not $backend.HasExited) { & taskkill /T /F /PID $backend.Id 2>&1 | Out-Null }
}

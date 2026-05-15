<#
.SYNOPSIS
    Build Seraphim desktop app — produces setup.exe + portable zip.

.DESCRIPTION
    1. Build React frontend  (npm run build)
    2. Bundle Python backend (pyinstaller seraphim.spec)
    3. Build Tauri app       (npm run tauri build)
    4. Zip portable bundle

.PARAMETER SkipFrontend
    Skip the React build step (use existing dist/).

.PARAMETER SkipBackend
    Skip the PyInstaller step (use existing resources/seraphim-server/).

.EXAMPLE
    .\scripts\build.ps1
    .\scripts\build.ps1 -SkipBackend   # frontend + Tauri only
#>

param(
    [switch]$SkipFrontend,
    [switch]$SkipBackend
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$UI   = Join-Path $Root "seraphim-ui"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    OK: $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "    FAIL: $msg" -ForegroundColor Red; exit 1 }

# ── Prerequisite checks ───────────────────────────────────────────────────────

Step "Checking prerequisites"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Fail "node not found — install Node.js from https://nodejs.org"
}
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    Fail "cargo not found — install Rust from https://rustup.rs"
}

# Resolve Python — prefer project venv, fall back to system python
$VenvPython = Join-Path $Root ".venv" "Scripts" "python.exe"
if (Test-Path $VenvPython) {
    $Python = $VenvPython
} else {
    $Python = (Get-Command python -ErrorAction SilentlyContinue)?.Source
    if (-not $Python) { Fail "python not found — install Python 3.10+ from https://python.org" }
}
Write-Host "    Using Python: $Python" -ForegroundColor DarkGray

# Check PyInstaller
& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Step "Installing PyInstaller into venv"
    & $Python -m pip install pyinstaller --quiet
}

# Check Tauri CLI
$tauriCli = Join-Path $UI "node_modules" ".bin" "tauri"
if (-not (Test-Path $tauriCli)) {
    Step "Installing npm dependencies"
    Push-Location $UI
    npm install --silent
    Pop-Location
}

Ok "Prerequisites OK"

# ── Step 1: React frontend build ──────────────────────────────────────────────

if (-not $SkipFrontend) {
    Step "Building React frontend"
    Push-Location $UI
    npm run build
    if ($LASTEXITCODE -ne 0) { Fail "React build failed" }
    Pop-Location
    Ok "React built -> seraphim-ui/dist/"
} else {
    Write-Host "    [skipped] Using existing dist/" -ForegroundColor Yellow
}

# ── Step 2: Python backend (PyInstaller) ─────────────────────────────────────

if (-not $SkipBackend) {
    Step "Bundling Python backend with PyInstaller"
    Push-Location $Root

    $resourcesDir = Join-Path $UI "src-tauri" "resources" "seraphim-server"
    if (Test-Path $resourcesDir) {
        Remove-Item $resourcesDir -Recurse -Force
    }

    $distPath = Join-Path $UI "src-tauri" "resources"
    & $Python -m PyInstaller seraphim.spec --noconfirm --clean --distpath $distPath
    if ($LASTEXITCODE -ne 0) { Fail "PyInstaller failed" }
    Pop-Location
    Ok "Backend bundled -> seraphim-ui/src-tauri/resources/seraphim-server/"
} else {
    Write-Host "    [skipped] Using existing resources/seraphim-server/" -ForegroundColor Yellow
    $resourcesDir = Join-Path $UI "src-tauri" "resources" "seraphim-server"
    if (-not (Test-Path $resourcesDir)) {
        Fail "resources/seraphim-server/ not found — run without -SkipBackend first"
    }
}

# ── Step 3: Tauri build ───────────────────────────────────────────────────────

Step "Building Tauri app"
Push-Location $UI
npm run tauri -- build
if ($LASTEXITCODE -ne 0) { Fail "Tauri build failed" }
Pop-Location

$bundleDir = Join-Path $UI "src-tauri" "target" "release" "bundle"
Ok "Tauri built -> $bundleDir"

# ── Step 4: Portable zip ──────────────────────────────────────────────────────

Step "Creating portable zip"
$releaseExe = Join-Path $UI "src-tauri" "target" "release" "seraphim-ui.exe"
$portableDir = Join-Path $Root "dist" "Seraphim-portable"
$portableZip = Join-Path $Root "dist" "Seraphim-portable.zip"

if (Test-Path $releaseExe) {
    New-Item -ItemType Directory -Force $portableDir | Out-Null
    Copy-Item $releaseExe $portableDir
    # Copy the Python backend sidecar next to the exe
    $sidecarSrc = Join-Path $UI "src-tauri" "target" "release" "seraphim-server"
    if (Test-Path $sidecarSrc) {
        Copy-Item $sidecarSrc $portableDir -Recurse -Force
    }
    # Copy default config
    $configSrc = Join-Path $Root "configs"
    Copy-Item $configSrc (Join-Path $portableDir "configs") -Recurse -Force

    if (Test-Path $portableZip) { Remove-Item $portableZip }
    Compress-Archive -Path "$portableDir\*" -DestinationPath $portableZip
    Remove-Item $portableDir -Recurse -Force
    Ok "Portable zip -> dist/Seraphim-portable.zip"
} else {
    Write-Host "    [warn] Release exe not found at expected path, skipping zip" -ForegroundColor Yellow
}

# ── Summary ───────────────────────────────────────────────────────────────────

Step "Build complete"
Write-Host ""
Write-Host "  Installer (NSIS setup.exe):" -ForegroundColor White
$nsisPath = Join-Path $bundleDir "nsis"
if (Test-Path $nsisPath) {
    Get-ChildItem $nsisPath -Filter "*.exe" | ForEach-Object {
        Write-Host "    $($_.FullName)" -ForegroundColor Green
    }
}
Write-Host ""
Write-Host "  MSI installer:" -ForegroundColor White
$msiPath = Join-Path $bundleDir "msi"
if (Test-Path $msiPath) {
    Get-ChildItem $msiPath -Filter "*.msi" | ForEach-Object {
        Write-Host "    $($_.FullName)" -ForegroundColor Green
    }
}
Write-Host ""
Write-Host "  Portable zip:" -ForegroundColor White
if (Test-Path $portableZip) {
    Write-Host "    $portableZip" -ForegroundColor Green
}
Write-Host ""

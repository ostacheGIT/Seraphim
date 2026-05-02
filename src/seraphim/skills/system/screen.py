"""Screen capture, OCR, and visual description skills.

Screenshot: PowerShell System.Drawing (zero Python deps, Windows only)
OCR:       Windows WinRT OCR via PowerShell (Windows 10/11 native, no install)
Describe:  Ollama vision model (llava / llava-phi3) via HTTP — optional
"""

from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import time
from pathlib import Path

from seraphim.skills.base import BaseSkill, SkillResult

# ── PowerShell scripts ────────────────────────────────────────────────────────

_PS_SCREENSHOT = r"""
param([string]$OutPath, [int]$X=0, [int]$Y=0, [int]$W=0, [int]$H=0)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
if ($W -eq 0) { $W = $screen.Width }
if ($H -eq 0) { $H = $screen.Height }
$bmp = New-Object System.Drawing.Bitmap($W, $H)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($X, $Y, 0, 0, (New-Object System.Drawing.Size($W, $H)))
$bmp.Save($OutPath, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output $OutPath
"""

_PS_OCR = r"""
param([string]$ImagePath)
[void][System.Reflection.Assembly]::LoadWithPartialName("System.Runtime.WindowsRuntime")
$null = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics,ContentType=WindowsRuntime]

function Await($Task) {
    $methods = [System.WindowsRuntimeSystemExtensions].GetMethods()
    $asTask  = $methods | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and !$_.IsGenericMethod } | Select-Object -First 1
    $net = $asTask.Invoke($null, @($Task))
    $net.Wait(-1) | Out-Null
    $net.Result
}

$absPath = (Resolve-Path $ImagePath).Path
$file    = Await([Windows.Storage.StorageFile]::GetFileFromPathAsync($absPath))
$stream  = Await($file.OpenAsync([Windows.Storage.FileAccessMode]::Read))
$decoder = Await([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream))
$bitmap  = Await($decoder.GetSoftwareBitmapAsync())
$engine  = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { Write-Error "WinRT OCR engine not available"; exit 1 }
$result  = Await($engine.RecognizeAsync($bitmap))
Write-Output $result.Text
"""


def _run_ps(script: str, args: list[str], timeout: int = 30) -> tuple[bool, str]:
    """Run an inline PowerShell script. Returns (success, output)."""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script] + args,
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "unknown error").strip()
            return False, err
        return True, (proc.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return False, f"Timeout after {timeout}s"
    except Exception as e:
        return False, str(e)


def _screenshot(out_path: str, x: int = 0, y: int = 0, w: int = 0, h: int = 0) -> tuple[bool, str]:
    script = _PS_SCREENSHOT + f"\nCapture-Screen -OutPath '{out_path}' -X {x} -Y {y} -W {w} -H {h}"
    # Inline: define and call
    script = (
        _PS_SCREENSHOT.replace("param([string]$OutPath, [int]$X=0, [int]$Y=0, [int]$W=0, [int]$H=0)", "")
        + f"\n$OutPath='{out_path}'; $X={x}; $Y={y}; $W={w}; $H={h}\n"
    )
    # Re-build cleanly
    body = "\n".join(_PS_SCREENSHOT.strip().splitlines()[1:])  # skip param line
    full = f"$OutPath='{out_path}'\n$X={x}\n$Y={y}\n$W={w}\n$H={h}\n" + body
    return _run_ps(full, [], timeout=15)


def _take_screenshot(region: dict | None = None) -> tuple[bool, str, str]:
    """Take screenshot → temp PNG. Returns (ok, path, error)."""
    tmp = Path(tempfile.gettempdir()) / f"seraphim_screen_{int(time.time())}.png"
    x = region.get("x", 0) if region else 0
    y = region.get("y", 0) if region else 0
    w = region.get("width", 0) if region else 0
    h = region.get("height", 0) if region else 0

    ok, out = _screenshot(str(tmp), x, y, w, h)
    if not ok:
        return False, "", out
    if not tmp.exists():
        return False, "", "Screenshot file not created"
    return True, str(tmp), ""


# ── Skills ────────────────────────────────────────────────────────────────────

class ScreenCaptureSkill(BaseSkill):
    name = "screen_capture"
    description = (
        "Take a screenshot of the screen (or a region) and save it as PNG. "
        "Returns the file path. Use before screen_ocr or screen_describe."
    )
    parameters = {
        "type": "object",
        "properties": {
            "output_path": {
                "type": "string",
                "description": "Where to save the PNG (default: temp file)",
                "default": "",
            },
            "region": {
                "type": "object",
                "description": "Optional region: {x, y, width, height} in pixels",
            },
        },
        "required": [],
    }

    async def run(self, output_path: str = "", region: dict | None = None, **kwargs) -> SkillResult:
        if output_path:
            tmp = Path(output_path)
        else:
            tmp = Path(tempfile.gettempdir()) / f"seraphim_screen_{int(time.time())}.png"

        x = region.get("x", 0) if region else 0
        y = region.get("y", 0) if region else 0
        w = region.get("width", 0) if region else 0
        h = region.get("height", 0) if region else 0
        ok, out = _screenshot(str(tmp), x, y, w, h)

        if not ok:
            return SkillResult(success=False, output="", error=f"Screenshot failed: {out}")
        if not tmp.exists():
            return SkillResult(success=False, output="", error="Screenshot file not created")
        size_kb = tmp.stat().st_size // 1024
        return SkillResult(success=True, output=str(tmp), data={"path": str(tmp), "size_kb": size_kb})


class ScreenOCRSkill(BaseSkill):
    name = "screen_ocr"
    description = (
        "Capture the screen and extract all visible text using Windows built-in OCR. "
        "Use to read text from apps, error messages, documents, or any on-screen content."
    )
    parameters = {
        "type": "object",
        "properties": {
            "region": {
                "type": "object",
                "description": "Optional region: {x, y, width, height}",
            },
            "image_path": {
                "type": "string",
                "description": "Path to an existing PNG to OCR (skips capture step)",
                "default": "",
            },
        },
        "required": [],
    }

    async def run(self, region: dict | None = None, image_path: str = "", **kwargs) -> SkillResult:
        # Step 1 — capture (unless image_path given)
        if image_path and Path(image_path).exists():
            png_path = image_path
            cleanup = False
        else:
            ok, png_path, err = _take_screenshot(region)
            if not ok:
                return SkillResult(success=False, output="", error=err)
            cleanup = True

        # Step 2 — WinRT OCR
        body = "\n".join(_PS_OCR.strip().splitlines()[1:])  # skip param line
        full_script = f"$ImagePath='{png_path}'\n" + body
        ok, text = _run_ps(full_script, [], timeout=20)

        if cleanup:
            try:
                Path(png_path).unlink(missing_ok=True)
            except Exception:
                pass

        if not ok:
            return SkillResult(success=False, output="", error=f"OCR failed: {text}")
        if not text:
            return SkillResult(success=True, output="(no text detected on screen)")
        return SkillResult(success=True, output=text)


class ScreenDescribeSkill(BaseSkill):
    name = "screen_describe"
    description = (
        "Capture the screen and describe its contents using a vision AI model (LLaVA via Ollama). "
        "Use when you need to understand what's visible on screen beyond just text — "
        "UI layout, images, charts, errors with visual context."
    )
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "What to ask about the screen (default: 'Describe what you see on this screen')",
                "default": "Describe what you see on this screen in detail.",
            },
            "model": {
                "type": "string",
                "description": "Vision model to use (default: llava)",
                "default": "llava",
            },
            "region": {
                "type": "object",
                "description": "Optional region: {x, y, width, height}",
            },
        },
        "required": [],
    }

    async def run(
        self,
        prompt: str = "Describe what you see on this screen in detail.",
        model: str = "llava",
        region: dict | None = None,
        **kwargs,
    ) -> SkillResult:
        import urllib.request

        # Step 1 — capture
        ok, png_path, err = _take_screenshot(region)
        if not ok:
            return SkillResult(success=False, output="", error=err)

        try:
            # Step 2 — encode image
            img_b64 = base64.b64encode(Path(png_path).read_bytes()).decode()

            # Step 3 — Ollama vision API
            from seraphim.settings import settings
            base_url = settings.engine.base_url.rstrip("/")
            payload = json.dumps({
                "model": model,
                "prompt": prompt,
                "images": [img_b64],
                "stream": False,
            }).encode()

            req = urllib.request.Request(
                f"{base_url}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            response = data.get("response", "").strip()
            if not response:
                return SkillResult(success=False, output="", error="Vision model returned empty response")
            return SkillResult(success=True, output=response)

        except Exception as e:
            return SkillResult(
                success=False, output="",
                error=f"Vision model error: {e}. Make sure 'llava' or another vision model is pulled in Ollama.",
            )
        finally:
            try:
                Path(png_path).unlink(missing_ok=True)
            except Exception:
                pass

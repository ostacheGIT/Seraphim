"""Desktop notifications for Seraphim monitor — Windows toast via PowerShell fallback."""

from __future__ import annotations
import subprocess
import sys


def notify(title: str, message: str) -> None:
    """Show a desktop notification. Tries plyer, falls back to PowerShell toast."""
    try:
        from plyer import notification  # type: ignore
        notification.notify(title=title, message=message[:256], app_name="Seraphim", timeout=8)
        return
    except Exception:
        pass

    if sys.platform == "win32":
        _powershell_toast(title, message)
    else:
        print(f"\n[SERAPHIM MONITOR] {title}: {message}\n")


def _powershell_toast(title: str, message: str) -> None:
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] | Out-Null\n"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
        "ContentType = WindowsRuntime] | Out-Null\n"
        f"$xml = [Windows.UI.Notifications.ToastNotificationManager]"
        f"::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)\n"
        f"$xml.GetElementsByTagName('text')[0].InnerText = '{title[:64]}'\n"
        f"$xml.GetElementsByTagName('text')[1].InnerText = '{message[:128]}'\n"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)\n"
        "$notifier = [Windows.UI.Notifications.ToastNotificationManager]"
        "::CreateToastNotifier('Seraphim')\n"
        "$notifier.Show($toast)"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=5,
        )
    except Exception:
        print(f"\n[SERAPHIM MONITOR] {title}: {message}\n")

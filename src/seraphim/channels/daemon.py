"""Channel daemon — background process that runs all enabled channels."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

_DIR = Path.home() / ".seraphim" / "channels"
PID_FILE   = _DIR / "channel_daemon.pid"
STOP_FILE  = _DIR / "channel_daemon.stop"
STATE_FILE = _DIR / "channel_daemon.state"
LOG_FILE   = _DIR / "channel_daemon.log"


def _setup_logging() -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_FILE),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _write_pid() -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def _write_state(state: dict) -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except Exception:
        return {}


def is_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong(0)
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return exit_code.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


async def start_daemon() -> None:
    """In-process daemon — starts all enabled channels as asyncio tasks."""
    import seraphim.channels.telegram  # noqa: F401 — triggers @ChannelRegistry.register
    import seraphim.channels.slack     # noqa: F401

    from seraphim.channels.base import ChannelRegistry
    from seraphim.channels.handler import handle_channel_message

    log = logging.getLogger("seraphim.channel_daemon")
    enabled = ChannelRegistry.get_enabled()
    if not enabled:
        log.info("No channels enabled — daemon idle")
        return

    channel_instances: list = []
    channel_states: dict = {}

    for name in enabled:
        try:
            ch = ChannelRegistry.get(name)()
            await ch.start(handle_channel_message)
            channel_instances.append(ch)
            channel_states[name] = {
                "status": "running",
                "started_at": datetime.now().isoformat(),
            }
            log.info("Channel '%s' started", name)
        except Exception as exc:
            log.error("Failed to start channel '%s': %s", name, exc)
            channel_states[name] = {"status": "error", "error": str(exc)}

    _write_state({
        "started_at": datetime.now().isoformat(),
        "channels": channel_states,
    })

    # Keep alive — poll for stop file every 60s
    while True:
        if STOP_FILE.exists():
            STOP_FILE.unlink(missing_ok=True)
            break
        await asyncio.sleep(60)

    for ch in channel_instances:
        try:
            await ch.stop()
        except Exception:
            pass
    _write_state({"status": "stopped"})
    log.info("Channel daemon stopped")


def main() -> None:
    """Subprocess entrypoint — called via `python -m seraphim.channels.daemon`."""
    _DIR.mkdir(parents=True, exist_ok=True)
    log_fh = open(LOG_FILE, "a", buffering=1, encoding="utf-8")
    sys.stdout = log_fh
    sys.stderr = log_fh
    _setup_logging()
    _write_pid()

    log = logging.getLogger("seraphim.channel_daemon")
    try:
        asyncio.run(start_daemon())
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as exc:
        log.exception("Daemon crashed: %s", exc)
    finally:
        PID_FILE.unlink(missing_ok=True)
        _write_state({"status": "stopped"})
        log.info("Daemon stopped.")


if __name__ == "__main__":
    main()

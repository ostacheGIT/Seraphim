"""Background learning daemon — runs as detached subprocess.

Launched by `seraphim learn daemon start`.
Reads config from ~/.seraphim/daemon_config.json,
writes state to ~/.seraphim/daemon_state.json,
logs to ~/.seraphim/daemon.log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

_DIR = Path.home() / ".seraphim"
PID_FILE = _DIR / "daemon.pid"
LOG_FILE = _DIR / "daemon.log"
STATE_FILE = _DIR / "daemon_state.json"
CONFIG_FILE = _DIR / "daemon_config.json"


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


async def _daemon_loop(config: dict) -> None:
    from seraphim.learning.orchestrator import LearningConfig, run_learning_loop
    from seraphim.learning.trace_store import trace_stats

    interval_secs = float(config.get("interval_hours", 6.0)) * 3600
    agents = [a.strip() for a in config.get("agents", "react,chat").split(",")]
    min_quality = float(config.get("min_quality", 0.6))
    min_new_traces = int(config.get("min_new_traces", 3))
    run_grpo = bool(config.get("run_grpo", False))
    grpo_generations = int(config.get("grpo_generations", 4))
    grpo_max_prompts = int(config.get("grpo_max_prompts", 30))
    run_finetune = bool(config.get("run_finetune", False))

    log = logging.getLogger("seraphim.daemon")
    pid = os.getpid()
    started_at = datetime.now().isoformat()
    run_count = 0
    last_result: dict = {}

    log.info(
        "Daemon started — pid=%d interval=%.1fh agents=%s grpo=%s",
        pid, config.get("interval_hours", 6), agents, run_grpo,
    )

    stats0 = await trace_stats()
    last_total = stats0["total_traces"]

    while True:
        next_run_dt = datetime.now() + timedelta(seconds=interval_secs)
        _write_state({
            "pid": pid,
            "started_at": started_at,
            "status": "sleeping",
            "run_count": run_count,
            "last_run": last_result.get("at"),
            "next_run": next_run_dt.isoformat(),
            "last_result": last_result,
            "config": config,
        })

        log.info("Sleeping until %s", next_run_dt.strftime("%Y-%m-%d %H:%M:%S"))
        await asyncio.sleep(interval_secs)

        stats = await trace_stats()
        new_traces = stats["total_traces"] - last_total
        if new_traces < min_new_traces:
            log.info("Only %d new traces (need %d) — skipping", new_traces, min_new_traces)
            continue

        last_total = stats["total_traces"]
        run_count += 1
        log.info("Run #%d — %d new traces since last run", run_count, new_traces)

        _write_state({
            "pid": pid,
            "started_at": started_at,
            "status": "training",
            "run_count": run_count,
            "last_run": datetime.now().isoformat(),
            "next_run": None,
            "last_result": last_result,
            "config": config,
        })

        try:
            cfg = LearningConfig(
                agents=agents,
                min_quality=min_quality,
                run_grpo=run_grpo,
                grpo_generations=grpo_generations,
                grpo_max_prompts=grpo_max_prompts,
                run_finetune=run_finetune,
            )
            result = await run_learning_loop(cfg)
            last_result = {
                "at": datetime.now().isoformat(),
                "mined": result.mined_pairs,
                "accepted": result.accepted,
                "rejected": result.rejected,
                "grpo_pairs": result.grpo_result.get("pairs_saved", 0) if result.grpo_result else 0,
                "grpo_reward": result.grpo_result.get("mean_reward", 0.0) if result.grpo_result else 0.0,
                "finetune_ok": result.finetune_result.get("success", False) if result.finetune_result else None,
                "error": None,
            }
            log.info(
                "Run #%d done: mined=%d accepted=%d rejected=%d",
                run_count, result.mined_pairs, result.accepted, result.rejected,
            )
            if result.grpo_result:
                g = result.grpo_result
                log.info(
                    "  GRPO: prompts=%d reward=%.3f pairs=%d",
                    g["prompts_used"], g["mean_reward"], g["pairs_saved"],
                )
        except Exception as exc:
            log.exception("Run #%d failed: %s", run_count, exc)
            last_result = {"at": datetime.now().isoformat(), "error": str(exc)}


def main() -> None:
    """Subprocess entrypoint — called by `python -m seraphim.learning.daemon`."""
    _DIR.mkdir(parents=True, exist_ok=True)
    # Redirect stdout/stderr to log file so any crash before logging setup is captured
    log_fh = open(LOG_FILE, "a", buffering=1, encoding="utf-8")
    sys.stdout = log_fh
    sys.stderr = log_fh

    _setup_logging()
    _write_pid()

    config: dict = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass

    try:
        asyncio.run(_daemon_loop(config))
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as exc:
        logging.getLogger("seraphim.daemon").exception("Daemon crashed: %s", exc)
    finally:
        PID_FILE.unlink(missing_ok=True)
        _write_state({"status": "stopped"})
        logging.getLogger("seraphim.daemon").info("Daemon stopped.")


if __name__ == "__main__":
    main()

"""Background learning daemon — runs as detached subprocess.

Launched by `seraphim learn daemon start`.
Reads config from ~/.seraphim/daemon_config.json,
writes state to ~/.seraphim/daemon_state.json,
logs to ~/.seraphim/daemon.log.

Graceful shutdown: create ~/.seraphim/daemon.stop (any content).
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
PID_FILE        = _DIR / "daemon.pid"
LOG_FILE        = _DIR / "daemon.log"
STATE_FILE      = _DIR / "daemon_state.json"
CONFIG_FILE     = _DIR / "daemon_config.json"
STOP_FILE       = _DIR / "daemon.stop"
CHECKPOINT_FILE = _DIR / "daemon_checkpoint.json"

# Training runs older than this are considered stale on restart
_STALE_CHECKPOINT_HOURS = 1


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


def _write_checkpoint(run_count: int, config: dict) -> None:
    CHECKPOINT_FILE.write_text(json.dumps({
        "run_count": run_count,
        "started_at": datetime.now().isoformat(),
        "config": config,
    }, indent=2, default=str))


def _clear_checkpoint() -> None:
    CHECKPOINT_FILE.unlink(missing_ok=True)


def _check_stale_checkpoint(log: logging.Logger) -> None:
    """Warn about a checkpoint left from a previous crash."""
    if not CHECKPOINT_FILE.exists():
        return
    try:
        data = json.loads(CHECKPOINT_FILE.read_text())
        started = datetime.fromisoformat(data.get("started_at", ""))
        age = datetime.now() - started
        if age > timedelta(hours=_STALE_CHECKPOINT_HOURS):
            log.warning(
                "Stale checkpoint found (run #%d started %s ago) — previous training may have crashed. Clearing.",
                data.get("run_count", "?"),
                str(age).split(".")[0],
            )
            _clear_checkpoint()
        else:
            log.info(
                "Recent checkpoint found (run #%d, %s ago) — resuming normally.",
                data.get("run_count", "?"),
                str(age).split(".")[0],
            )
    except Exception:
        log.warning("Could not parse checkpoint file — clearing it.")
        _clear_checkpoint()


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


async def _sleep_interruptible(seconds: float, poll_interval: float = 5.0) -> bool:
    """Sleep for `seconds`, waking every `poll_interval` to check for the stop file.

    Returns True if stopped early, False if the full duration elapsed.
    """
    elapsed = 0.0
    while elapsed < seconds:
        if STOP_FILE.exists():
            return True
        chunk = min(poll_interval, seconds - elapsed)
        await asyncio.sleep(chunk)
        elapsed += chunk
    return False


async def _daemon_loop(config: dict) -> None:
    from seraphim.learning.orchestrator import LearningConfig, run_learning_loop
    from seraphim.learning.trace_store import trace_stats

    interval_secs    = float(config.get("interval_hours", 6.0)) * 3600
    agents           = [a.strip() for a in config.get("agents", "react,chat").split(",")]
    min_quality      = float(config.get("min_quality", 0.6))
    min_new_traces   = int(config.get("min_new_traces", 3))
    run_grpo         = bool(config.get("run_grpo", False))
    grpo_generations = int(config.get("grpo_generations", 4))
    grpo_max_prompts = int(config.get("grpo_max_prompts", 30))
    run_finetune     = bool(config.get("run_finetune", False))
    timeout_secs     = float(config.get("training_timeout_hours", 2.0)) * 3600

    log = logging.getLogger("seraphim.daemon")
    pid = os.getpid()
    started_at = datetime.now().isoformat()
    run_count = 0
    last_result: dict = {}

    _check_stale_checkpoint(log)

    log.info(
        "Daemon started — pid=%d interval=%.1fh agents=%s grpo=%s timeout=%.1fh",
        pid, config.get("interval_hours", 6), agents, run_grpo, config.get("training_timeout_hours", 2.0),
    )
    log.info("Shutdown: create %s", STOP_FILE)

    stats0 = await trace_stats()
    last_total = max(0, stats0["total_traces"] - min_new_traces)

    while True:
        if STOP_FILE.exists():
            log.info("Stop file detected — shutting down gracefully.")
            STOP_FILE.unlink(missing_ok=True)
            break

        stats = await trace_stats()
        new_traces = stats["total_traces"] - last_total

        if new_traces < min_new_traces:
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
            log.info(
                "Only %d new traces (need %d) — sleeping until %s",
                new_traces, min_new_traces, next_run_dt.strftime("%Y-%m-%d %H:%M:%S"),
            )
            stopped = await _sleep_interruptible(interval_secs)
            if stopped:
                log.info("Stop file detected during sleep — shutting down gracefully.")
                STOP_FILE.unlink(missing_ok=True)
                break
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
        _write_checkpoint(run_count, config)

        try:
            cfg = LearningConfig(
                agents=agents,
                min_quality=min_quality,
                run_grpo=run_grpo,
                grpo_generations=grpo_generations,
                grpo_max_prompts=grpo_max_prompts,
                run_finetune=run_finetune,
            )
            result = await asyncio.wait_for(run_learning_loop(cfg), timeout=timeout_secs)
            _clear_checkpoint()
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
        except asyncio.TimeoutError:
            log.error(
                "Run #%d timed out after %.1f hours — killed. Checkpoint left for inspection.",
                run_count, timeout_secs / 3600,
            )
            last_result = {
                "at": datetime.now().isoformat(),
                "error": f"timeout after {timeout_secs/3600:.1f}h",
            }
        except Exception as exc:
            log.exception("Run #%d failed: %s", run_count, exc)
            last_result = {"at": datetime.now().isoformat(), "error": str(exc)}
            _clear_checkpoint()

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
        stopped = await _sleep_interruptible(interval_secs)
        if stopped:
            log.info("Stop file detected during sleep — shutting down gracefully.")
            STOP_FILE.unlink(missing_ok=True)
            break


def main() -> None:
    """Subprocess entrypoint — called by `python -m seraphim.learning.daemon`."""
    _DIR.mkdir(parents=True, exist_ok=True)
    log_fh = open(LOG_FILE, "a", buffering=1, encoding="utf-8")
    sys.stdout = log_fh
    sys.stderr = log_fh

    _setup_logging()
    _write_pid()

    log = logging.getLogger("seraphim.daemon")
    config: dict = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text())
        except Exception as exc:
            log.warning("Could not parse daemon config (%s) — using defaults.", exc)

    try:
        asyncio.run(_daemon_loop(config))
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

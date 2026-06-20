#!/usr/bin/python3

import logging
import os
import threading
import time
from typing import Callable

from src.core.config import CONFIG_PATH

logger: logging.Logger = logging.getLogger(__name__)

# Data directory shared between the agent and the web control panel
_DATA_DIR: str = os.path.dirname(CONFIG_PATH)

# Sentinel and signalling files on the shared data volume
HEARTBEAT_PATH: str = os.path.join(_DATA_DIR, ".agent-heartbeat")
RESTART_SENTINEL: str = os.path.join(_DATA_DIR, ".restart-request")
PAUSE_SENTINEL: str = os.path.join(_DATA_DIR, ".deriver-paused")

# Consider the agent alive if it wrote a heartbeat within this many seconds
HEARTBEAT_STALE_SECONDS: float = 45.0

# How often the agent refreshes its heartbeat
HEARTBEAT_INTERVAL_SECONDS: float = 10.0


def write_heartbeat() -> None:
    """
    This function writes the current timestamp to the heartbeat file so the web
    control panel can tell whether the agent process is alive.
    """
    try:
        with open(file=HEARTBEAT_PATH, mode="w") as f:
            f.write(str(time.time()))
    except OSError as exc:
        logger.debug("Failed to write heartbeat: %s", exc)


def read_heartbeat() -> float | None:
    """
    This function returns the last heartbeat timestamp, or None when unavailable.
    """
    try:
        with open(file=HEARTBEAT_PATH, mode="r") as f:
            return float(f.read().strip())
    except (OSError, ValueError):
        return None


def is_agent_alive() -> bool:
    """
    This function returns True when the most recent heartbeat is recent enough.
    """
    last: float | None = read_heartbeat()
    return last is not None and (time.time() - last) <= HEARTBEAT_STALE_SECONDS


def start_heartbeat(interval: float = HEARTBEAT_INTERVAL_SECONDS) -> None:
    """
    This function starts a daemon thread that refreshes the heartbeat periodically.
    """
    def _loop() -> None:
        while True:
            write_heartbeat()
            time.sleep(interval)

    write_heartbeat()
    thread: threading.Thread = threading.Thread(target=_loop, daemon=True, name="heartbeat")
    thread.start()


def request_restart() -> None:
    """
    This function writes the restart-request sentinel. The supervisor entrypoint
    polls this file and restarts the agent process when it appears.
    """
    with open(file=RESTART_SENTINEL, mode="w") as f:
        f.write(str(time.time()))


def restart_requested() -> bool:
    """
    This function returns True when a restart has been requested.
    """
    return os.path.isfile(RESTART_SENTINEL)


def pause_deriver() -> None:
    """
    This function creates the deriver pause sentinel so background reasoning halts.
    """
    with open(file=PAUSE_SENTINEL, mode="w") as f:
        f.write(str(time.time()))


def resume_deriver() -> None:
    """
    This function removes the deriver pause sentinel so background reasoning resumes.
    """
    if os.path.isfile(PAUSE_SENTINEL):
        os.remove(PAUSE_SENTINEL)


def is_deriver_paused() -> bool:
    """
    This function returns True when the deriver pause sentinel is present.
    """
    return os.path.isfile(PAUSE_SENTINEL)


def install_signal_handlers(on_shutdown: Callable[[], None]) -> None:
    """
    This function installs SIGTERM/SIGINT handlers that invoke on_shutdown once and
    then exit cleanly, so the supervisor can restart the agent gracefully.
    """
    import signal

    already_shutting_down: dict[str, bool] = {"flag": False}

    def _handler(signum: int, _frame: object) -> None:
        if already_shutting_down["flag"]:
            return
        already_shutting_down["flag"] = True
        logger.info("Received signal %d; shutting down", signum)
        try:
            on_shutdown()
        except Exception as exc:
            logger.error("Error during shutdown: %s", exc)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)

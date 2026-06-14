#!/usr/bin/python3

import json
import os
import threading
from typing import Any, Callable

from filelock import FileLock

# Config file location
CONFIG_PATH: str = "/home/memtrix/data/config.json"

# Lock for thread-safe config file read-modify-write operations (in-process)
CONFIG_LOCK: threading.Lock = threading.Lock()

# Cross-process lock file guarding config.json (shared between the agent and the
# web control panel, which run as separate processes on the same data volume)
CONFIG_FILE_LOCK_PATH: str = CONFIG_PATH + ".lock"
_CONFIG_FILE_LOCK: FileLock = FileLock(CONFIG_FILE_LOCK_PATH, timeout=15)


def load_config() -> dict[str, Any]:
    """
    This function reads and returns the config from disk under the cross-process lock.
    """
    with CONFIG_LOCK, _CONFIG_FILE_LOCK:
        with open(file=CONFIG_PATH, mode="r") as f:
            return json.load(fp=f)


def save_config(config: dict[str, Any]) -> None:
    """
    This function atomically writes the full config to disk under the cross-process
    lock. The write goes to a temporary file that is renamed into place so readers
    never observe a half-written file.
    """
    with CONFIG_LOCK, _CONFIG_FILE_LOCK:
        _atomic_write(config=config)


def update_config(mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """
    This function performs a locked read-modify-write: it loads the current config,
    passes it to the mutate callable for in-place modification, then writes it back.
    Returns the updated config. This is the safe way to change a subset of keys
    without clobbering concurrent changes (e.g. secrets resolved at runtime).
    """
    with CONFIG_LOCK, _CONFIG_FILE_LOCK:
        with open(file=CONFIG_PATH, mode="r") as f:
            config: dict[str, Any] = json.load(fp=f)
        mutate(config)
        _atomic_write(config=config)
        return config


def _atomic_write(config: dict[str, Any]) -> None:
    """
    This function writes the config to a temp file and renames it into place.
    Caller must already hold the locks.
    """
    tmp_path: str = CONFIG_PATH + ".tmp"
    with open(file=tmp_path, mode="w") as f:
        json.dump(obj=config, fp=f, indent=4, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(src=tmp_path, dst=CONFIG_PATH)

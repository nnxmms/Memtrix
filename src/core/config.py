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


def resolve_ssh_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    This function returns the SSH configuration merged with safe defaults so that
    installs without an "ssh" section keep working unchanged. When disabled, the
    SSH sysadmin tools are not loaded at all.
    """
    defaults: dict[str, Any] = {
        "enabled": True,            # load the SSH remote-administration tools
        "connect_timeout": 15,     # seconds to wait when opening a connection
        "command_timeout": 120,    # seconds to wait for a single command to finish
        "max_output_chars": 20000, # cap command output returned to the model
    }
    user_cfg: dict[str, Any] = config.get("ssh", {}) or {}
    return {**defaults, **user_cfg}


def resolve_skills_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    This function returns the skills configuration merged with safe defaults so that
    installs without a "skills" section keep working unchanged. When disabled, the
    skill management tool is not loaded and the skills catalog is not injected.
    """
    defaults: dict[str, Any] = {
        "enabled": True,  # load the skill_manage tool and show the skills catalog
    }
    user_cfg: dict[str, Any] = config.get("skills", {}) or {}
    return {**defaults, **user_cfg}


def resolve_agent_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    This function returns the core agent-loop configuration merged with safe
    defaults so that installs without an "agent" section keep working unchanged.
    """
    defaults: dict[str, Any] = {
        "max_iterations": 25,  # tool-call rounds allowed per request before forcing a final answer
        "max_history": 60,     # messages kept in a session before older turns are trimmed
    }
    user_cfg: dict[str, Any] = config.get("agent", {}) or {}
    return {**defaults, **user_cfg}


def resolve_prompt_guard_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    This function returns the prompt-injection screening configuration merged with
    safe defaults so that installs without a "prompt_guard" section keep working
    unchanged. When enabled, the output of the web-fetching tools (web_search and
    fetch_url) is screened with a local prompt-injection classifier before it reaches
    the conversation; flagged content is replaced with a tool-error.
    """
    defaults: dict[str, Any] = {
        "enabled": True,        # screen untrusted tool output for prompt injection
        "model": "deberta",     # short name (deberta) or a full HuggingFace repo id
        "threshold": 0.5,       # malicious-probability cutoff (0-1) to block content
        "max_chars": 20000,     # cap characters screened per tool result
        "fail_closed": False,   # block untrusted content if the screener errors / can't load
    }
    user_cfg: dict[str, Any] = config.get("prompt_guard", {}) or {}
    return {**defaults, **user_cfg}


def resolve_email_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    This function returns the email configuration merged with safe defaults so that
    installs without an "email" section keep working unchanged. When disabled, the
    email tools (check / mark unread / send) are not loaded at all. The password is
    expected to arrive already resolved from the EMAIL_PASSWORD secret.
    """
    defaults: dict[str, Any] = {
        "enabled": False,            # opt-in to mailbox access
        "imap_host": "",            # e.g. imap.gmail.com
        "imap_port": 993,
        "imap_ssl": True,            # implicit TLS (993); when false STARTTLS is used
        "smtp_host": "",            # e.g. smtp.gmail.com
        "smtp_port": 587,
        "smtp_security": "starttls", # "starttls" (587), "ssl" (465) or "none"
        "username": "",             # mailbox login (usually the full address)
        "from_address": "",         # defaults to the username when unset
        "from_name": "",            # display name; defaults to the agent's name when unset
        "password": "$EMAIL_PASSWORD",  # resolved from the EMAIL_PASSWORD secret
        "mailbox": "INBOX",
        "auto_mark_read": True,      # mark fetched messages read after retrieval
        "max_fetch": 10,             # default number of messages email_check returns
        "max_body_chars": 4000,      # cap body length returned to the model
    }
    user_cfg: dict[str, Any] = config.get("email", {}) or {}
    resolved: dict[str, Any] = {**defaults, **user_cfg}
    # Default the outgoing display name to the agent's configured name.
    if not str(resolved.get("from_name") or "").strip():
        resolved["from_name"] = config.get("main-agent", {}).get("name", "Memtrix")
    return resolved


def resolve_voice_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    This function returns the voice-transcription configuration merged with safe
    defaults. When disabled, Matrix audio messages are treated as regular files.
    """
    defaults: dict[str, Any] = {
        "enabled": False,            # opt-in to Matrix audio transcription
        "provider": "local",        # reserved for future providers; local for now
        "model": "base",            # faster-whisper model tier
        "language": None,            # auto-detect when unset
        "max_audio_bytes": 25_000_000,
        "timeout_seconds": 180,
    }
    user_cfg: dict[str, Any] = config.get("voice", {}) or {}
    return {**defaults, **user_cfg}


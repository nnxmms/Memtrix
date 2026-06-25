#!/usr/bin/python3

import json
import logging
import os
import re
import uuid
from datetime import date
from typing import Any

logger: logging.Logger = logging.getLogger(__name__)

# UUID v4 format validation
_UUID_PATTERN: re.Pattern[str] = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class Session:

    def __init__(self, sessions_dir: str, session_id: str | None = None, ephemeral: bool = False) -> None:
        """
        This is the Session class which manages short-term conversation history.
        Each session is stored as a JSON file in sessions_dir/yyyy-mm-dd/. When
        ephemeral is True the session lives entirely in memory and never touches
        disk — used by background worker agents, which keep nothing persistent.
        """
        # When True, history is held in memory only and no files are created or written.
        self._ephemeral: bool = ephemeral

        # Ephemeral sessions skip all disk setup and start with empty history.
        if ephemeral:
            self._sessions_dir = sessions_dir
            self._session_id = str(uuid.uuid4())
            self._path = ""
            self._history = []
            return

        # Sessions root directory
        self._sessions_dir: str = sessions_dir
        os.makedirs(self._sessions_dir, exist_ok=True)

        # Session id — use existing or create new
        existing_path: str | None = None
        if session_id and _UUID_PATTERN.match(session_id):
            existing_path = self._find_session(session_id=session_id)

        if existing_path:
            self._session_id: str = session_id
            self._path: str = existing_path
        else:
            self._session_id = str(uuid.uuid4())
            day_dir: str = os.path.join(self._sessions_dir, date.today().isoformat())
            os.makedirs(day_dir, exist_ok=True)
            self._path = os.path.join(day_dir, f"{self._session_id}.json")
            with open(file=self._path, mode="w") as f:
                json.dump(obj=[], fp=f)

        # Message history
        self._history: list[dict[str, Any]] = self._load_history()

    def _find_session(self, session_id: str) -> str | None:
        """
        This function searches date subdirectories for an existing session file.
        """
        for entry in os.listdir(self._sessions_dir):
            candidate: str = os.path.join(self._sessions_dir, entry, f"{session_id}.json")
            if os.path.isfile(candidate):
                return candidate
        return None

    @property
    def session_id(self) -> str:
        """
        This function returns the session id.
        """
        return self._session_id

    def _load_history(self) -> list[dict[str, Any]]:
        """
        This function loads the message history from the session file.
        """
        with open(file=self._path, mode="r") as f:
            try:
                data: Any = json.load(fp=f)
            except (json.JSONDecodeError, ValueError):
                data = None

        if isinstance(data, list):
            return data

        # File was corrupted — reset to empty history
        logger.warning("Corrupted session file %s — resetting", self._path)
        with open(file=self._path, mode="w") as f:
            json.dump(obj=[], fp=f)
        return []

    def _save_history(self) -> None:
        """
        This function persists the message history to the session file. Ephemeral
        (in-memory) sessions skip persistence entirely.
        """
        if self._ephemeral:
            return
        with open(file=self._path, mode="w") as f:
            json.dump(obj=self._history, fp=f, indent=2)

    @property
    def history(self) -> list[dict[str, Any]]:
        """
        This function returns the current message history.
        """
        return self._history

    def append(self, message: dict[str, Any]) -> None:
        """
        This function adds a message to the history and saves it.
        """
        self._history.append(message)
        self._save_history()

    def trim(self, max_messages: int = 50) -> None:
        """
        This function trims the session history to stay within a maximum message count.
        Keeps the system prompt (first message if role=system) plus the most recent
        messages, and never starts the retained window on an orphaned tool result so
        the history stays valid for strict providers (every tool message must follow
        its assistant tool-call message).
        """
        if len(self._history) <= max_messages:
            return

        has_system: bool = bool(self._history) and self._history[0].get("role") == "system"
        system: list[dict[str, Any]] = [self._history[0]] if has_system else []
        body: list[dict[str, Any]] = self._history[1:] if has_system else self._history

        keep: int = max(1, max_messages - len(system))
        tail: list[dict[str, Any]] = body[-keep:]

        # Drop leading orphaned tool results whose assistant tool-call was trimmed away.
        while tail and tail[0].get("role") == "tool":
            tail.pop(0)

        self._history = system + tail
        self._save_history()

    def set_system_prompt(self, content: str) -> None:
        """
        This function updates the leading system message in place (or inserts one when
        absent) so a refreshed system prompt propagates into an active session without
        disturbing the rest of the history.
        """
        if self._history and self._history[0].get("role") == "system":
            if self._history[0].get("content") == content:
                return
            self._history[0]["content"] = content
        else:
            self._history.insert(0, {"role": "system", "content": content})
        self._save_history()

    def extend(self, messages: list[dict[str, Any]]) -> None:
        """
        This function adds multiple messages to the history and saves it.
        """
        self._history.extend(messages)
        self._save_history()

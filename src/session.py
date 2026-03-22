#!/usr/bin/python3

import json
import os
import re
import uuid
from datetime import date
from typing import Any

# UUID v4 format validation
_UUID_PATTERN: re.Pattern[str] = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class Session:

    def __init__(self, sessions_dir: str, session_id: str | None = None) -> None:
        """
        This is the Session class which manages short-term conversation history.
        Each session is stored as a JSON file in sessions_dir/yyyy-mm-dd/.
        """
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
            return json.load(fp=f)

    def _save_history(self) -> None:
        """
        This function persists the message history to the session file.
        """
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

    def extend(self, messages: list[dict[str, Any]]) -> None:
        """
        This function adds multiple messages to the history and saves it.
        """
        self._history.extend(messages)
        self._save_history()

#!/usr/bin/python3

import json
import os
import uuid
from typing import Any


class Session:

    def __init__(self, sessions_dir: str, session_id: str | None = None) -> None:
        """
        This is the Session class which manages short-term conversation history.
        Each session is stored as a JSON file in the sessions directory.
        """
        # Sessions directory
        self._sessions_dir: str = sessions_dir
        os.makedirs(self._sessions_dir, exist_ok=True)

        # Session id — use existing or create new
        if session_id and os.path.isfile(os.path.join(sessions_dir, f"{session_id}.json")):
            self._session_id: str = session_id
        else:
            self._session_id = str(uuid.uuid4())
            # Create empty session file
            path: str = os.path.join(self._sessions_dir, f"{self._session_id}.json")
            with open(file=path, mode="w") as f:
                json.dump(obj=[], fp=f)

        # Message history
        self._history: list[dict[str, Any]] = self._load_history()

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
        path: str = os.path.join(self._sessions_dir, f"{self._session_id}.json")
        with open(file=path, mode="r") as f:
            return json.load(fp=f)

    def _save_history(self) -> None:
        """
        This function persists the message history to the session file.
        """
        path: str = os.path.join(self._sessions_dir, f"{self._session_id}.json")
        with open(file=path, mode="w") as f:
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

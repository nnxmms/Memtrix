#!/usr/bin/python3

from typing import Any

from src.integrations.mail import EmailError, EmailManager
from src.tools.base import BaseTool


class EmailMarkUnreadTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the EmailMarkUnreadTool which restores messages to the unread state.
        """
        self._workspace_dir: str = workspace_dir
        self._email_manager: EmailManager | None = None
        super().__init__(
            name="email_mark_unread",
            description=(
                "Mark one or more messages as unread again, using the UIDs returned by email_check. "
                "Useful when a message was auto-marked read on retrieval but should stay flagged for "
                "the user to read themselves."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "message_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "The message UIDs to mark unread (from email_check).",
                    },
                },
                "required": ["message_ids"],
            },
        )

    def set_email_manager(self, manager: EmailManager) -> None:
        """
        This function injects the configured EmailManager (called at startup).
        """
        self._email_manager = manager

    def execute(self, **kwargs: Any) -> str:
        """
        This function clears the read flag on the given message UIDs.
        """
        if self._email_manager is None:
            return "Error: email is not enabled."

        raw_ids: Any = kwargs.get("message_ids")
        if isinstance(raw_ids, str):
            raw_ids = [raw_ids]
        if not isinstance(raw_ids, list) or not raw_ids:
            return "Error: provide message_ids as a list of UIDs from email_check."
        uids: list[str] = [str(uid) for uid in raw_ids]

        try:
            updated: int = self._email_manager.mark_unread(uids=uids)
        except EmailError as exc:
            return f"Error: {exc}"

        if updated == 0:
            return "No messages were updated (check the UIDs)."
        return f"Marked {updated} message(s) as unread."

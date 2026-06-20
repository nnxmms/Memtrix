#!/usr/bin/python3

import os
from typing import Any, Callable

from src.tools.base import BaseTool


class SendFileTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the SendFileTool which sends a file from the workspace to the user via the channel.
        """
        self._workspace_dir: str = workspace_dir
        self._send_file: Callable[[str], None] | None = None
        super().__init__(
            name="send_file",
            description="Send a file from the workspace to the user. The path is relative to the workspace directory (e.g. 'attachments/report.txt').",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path relative to the workspace directory."
                    }
                },
                "required": ["path"]
            }
        )

    def set_send_file(self, callback: Callable[[str], None] | None) -> None:
        """
        This function sets the callback for sending files to the channel.
        """
        self._send_file = callback

    def execute(self, **kwargs: Any) -> str:
        """
        This function sends a file to the user via the channel callback.
        """
        path: str = kwargs.get("path", "")
        if not path:
            return "Error: path cannot be empty."

        filepath: str = os.path.join(self._workspace_dir, path)

        # Prevent path traversal
        if not os.path.realpath(filepath).startswith(os.path.realpath(self._workspace_dir)):
            return "Error: path must be within the workspace directory."

        if not os.path.isfile(filepath):
            return f"Error: file not found: {path}"

        if not self._send_file:
            return "Error: file sending is not available on this channel."

        self._send_file(filepath)
        return f"File sent: {path}"

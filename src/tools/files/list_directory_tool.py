#!/usr/bin/python3

import os
from typing import Any

from src.tools.base import BaseTool


class ListDirectoryTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the ListDirectoryTool which lists the contents of a directory in the workspace.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="list_directory",
            description="List the contents of a directory in the workspace. Returns file and subdirectory names. Defaults to the workspace root if no path is given.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The directory path relative to the workspace, e.g. attachments/. Leave empty for the workspace root."
                    }
                },
                "required": []
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function lists the contents of a directory in the workspace.
        """
        path: str = kwargs.get("path", "")

        dirpath: str = os.path.join(self._workspace_dir, path) if path else self._workspace_dir

        # Prevent path traversal
        if not os.path.realpath(dirpath).startswith(os.path.realpath(self._workspace_dir)):
            return "Error: path must be within the workspace directory."

        if not os.path.isdir(dirpath):
            return f"Error: directory not found: {path or '/'}"

        entries: list[str] = sorted(os.listdir(dirpath))
        if not entries:
            return "(empty directory)"

        lines: list[str] = []
        for entry in entries:
            full: str = os.path.join(dirpath, entry)
            suffix: str = "/" if os.path.isdir(full) else ""
            lines.append(f"{entry}{suffix}")

        return "\n".join(lines)

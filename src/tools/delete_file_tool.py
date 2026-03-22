#!/usr/bin/python3

import os
from typing import Any

from src.tools.base import BaseTool

# Files that must not be deleted
BLOCKED_FILES: set[str] = {"AGENT.md", "BEHAVIOR.md", "MEMORY.md", "SOUL.md", "USER.md"}
BLOCKED_DIRS: set[str] = {"memory"}


class DeleteFileTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the DeleteFileTool which deletes files from the workspace.
        Core persona files and memory files are protected.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="delete_file",
            description=(
                "Delete a file from the workspace. "
                "Cannot delete core persona files or memory files. WARNING: this action cannot be reverted."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path relative to the workspace, e.g. attachments/old-report.pdf."
                    }
                },
                "required": ["path"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function deletes a file from the workspace.
        """
        path: str = kwargs.get("path", "")
        if not path:
            return "Error: path cannot be empty."

        filepath: str = os.path.join(self._workspace_dir, path)

        # Prevent path traversal
        if not os.path.realpath(filepath).startswith(os.path.realpath(self._workspace_dir)):
            return "Error: path must be within the workspace directory."

        # Block core persona files
        basename: str = os.path.basename(filepath)
        if basename in BLOCKED_FILES:
            return f"Error: '{basename}' is a core file and cannot be deleted."

        # Block memory directory files
        relpath: str = os.path.relpath(filepath, self._workspace_dir)
        if relpath.startswith("memory" + os.sep) or relpath.startswith("memory/"):
            return "Error: memory files cannot be deleted."

        if not os.path.isfile(path=filepath):
            return f"Error: file not found: {path}"

        os.remove(path=filepath)
        return f"Deleted file: {path}"

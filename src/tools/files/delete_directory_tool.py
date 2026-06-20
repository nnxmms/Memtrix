#!/usr/bin/python3

import os
import shutil
from typing import Any

from src.tools.base import BaseTool

# Directories that must not be deleted
BLOCKED_DIRS: set[str] = {"memory", "attachments", "downloads"}


class DeleteDirectoryTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the DeleteDirectoryTool which deletes directories from the workspace.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="delete_directory",
            description="Delete a directory and all its contents from the workspace. Cannot delete protected directories (memory/, attachments/, downloads/). WARNING: this action cannot be reverted.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The directory path relative to the workspace, e.g. projects/old-report."
                    }
                },
                "required": ["path"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function deletes a directory from the workspace.
        """
        path: str = kwargs.get("path", "")
        if not path:
            return "Error: path cannot be empty."

        dirpath: str = os.path.join(self._workspace_dir, path)

        # Prevent path traversal
        if not os.path.realpath(dirpath).startswith(os.path.realpath(self._workspace_dir)):
            return "Error: path must be within the workspace directory."

        # Prevent deleting the workspace root itself
        if os.path.realpath(dirpath) == os.path.realpath(self._workspace_dir):
            return "Error: cannot delete the workspace root."

        # Block protected directories
        relpath: str = os.path.relpath(dirpath, self._workspace_dir)
        top_level: str = relpath.split(os.sep)[0]
        if top_level in BLOCKED_DIRS:
            return f"Error: '{top_level}/' is protected and cannot be deleted."

        if not os.path.isdir(dirpath):
            return f"Error: directory not found: {path}"

        shutil.rmtree(path=dirpath)
        return f"Deleted directory: {path}"

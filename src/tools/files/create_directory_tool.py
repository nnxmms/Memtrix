#!/usr/bin/python3

import os
from typing import Any

from src.tools.base import BaseTool

# Directories that must not be created at the workspace root
BLOCKED_DIRS: set[str] = {"memory"}


class CreateDirectoryTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the CreateDirectoryTool which creates directories inside the workspace.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="create_directory",
            description="Create a directory inside the workspace. Supports nested paths (parent directories are created automatically).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The directory path relative to the workspace, e.g. projects/report."
                    }
                },
                "required": ["path"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function creates a directory inside the workspace.
        """
        path: str = kwargs.get("path", "")
        if not path:
            return "Error: path cannot be empty."

        dirpath: str = os.path.join(self._workspace_dir, path)

        # Prevent path traversal
        if not os.path.realpath(dirpath).startswith(os.path.realpath(self._workspace_dir)):
            return "Error: path must be within the workspace directory."

        # Block protected directories
        relpath: str = os.path.relpath(dirpath, self._workspace_dir)
        top_level: str = relpath.split(os.sep)[0]
        if top_level in BLOCKED_DIRS:
            return f"Error: '{top_level}/' is managed by dedicated tools."

        if os.path.isdir(s=dirpath):
            return f"Directory already exists: {path}"

        os.makedirs(name=dirpath, exist_ok=True)
        return f"Created directory: {path}"

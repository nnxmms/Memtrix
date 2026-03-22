#!/usr/bin/python3

import os
from typing import Any

from src.tools.base import BaseTool
from src.tools.utils import confirm_with_user

# Files that must be managed via dedicated tools
BLOCKED_FILES: set[str] = {"AGENT.md", "BEHAVIOR.md", "MEMORY.md", "SOUL.md", "USER.md"}
BLOCKED_DIRS: set[str] = {"memory"}


class CreateFileTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the CreateFileTool which creates or overwrites text files in the workspace.
        Core persona files and memory files are excluded — use the dedicated tools for those.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="create_file",
            description=(
                "Create or overwrite a text file in the workspace. Parent directories are created automatically. "
                "Cannot write core persona files or memory files — use the dedicated tools for those."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path relative to the workspace, e.g. projects/notes.txt."
                    },
                    "content": {
                        "type": "string",
                        "description": "The text content to write to the file."
                    }
                },
                "required": ["path", "content"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function creates or overwrites a text file in the workspace.
        """
        path: str = kwargs.get("path", "")
        content: str = kwargs.get("content", "")
        if not path:
            return "Error: path cannot be empty."

        filepath: str = os.path.join(self._workspace_dir, path)

        # Prevent path traversal
        if not os.path.realpath(filepath).startswith(os.path.realpath(self._workspace_dir)):
            return "Error: path must be within the workspace directory."

        # Block core persona files
        basename: str = os.path.basename(filepath)
        if basename in BLOCKED_FILES:
            return f"Error: '{basename}' is a core file. Use write_core_file instead."

        # Block memory directory files
        relpath: str = os.path.relpath(filepath, self._workspace_dir)
        if relpath.startswith("memory" + os.sep) or relpath.startswith("memory/"):
            return "Error: memory files must be written via write_memory_file."

        # Confirm overwrite if file already exists
        if os.path.isfile(path=filepath):
            if not confirm_with_user(kwargs, message=f"⚠️ Memtrix wants to overwrite an existing file:\n\n  File: {path}\n\nAllow this? (yes/no)"):
                return "File overwrite denied by user."

        # Create parent directories if needed
        parent: str = os.path.dirname(filepath)
        if parent:
            os.makedirs(name=parent, exist_ok=True)

        with open(file=filepath, mode="w") as f:
            f.write(content)

        return f"Created file: {path}"

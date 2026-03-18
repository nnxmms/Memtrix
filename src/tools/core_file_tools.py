#!/usr/bin/python3

import os
from typing import Any

from src.tools.base import BaseTool

# Only these files can be read/written by the core file tools
ALLOWED_FILES: set[str] = {"BEHAVIOR.md", "SOUL.md", "USER.md", "MEMORY.md"}


class ReadCoreFileTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the ReadCoreFileTool which reads one of the core persona files.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="read_core_file",
            description="Read the current content of a core persona file. Must be called before writing. Allowed files: BEHAVIOR.md, SOUL.md, USER.md, MEMORY.md",
            parameters={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "The core file to read (BEHAVIOR.md, SOUL.md, USER.md, or MEMORY.md)."
                    }
                },
                "required": ["filename"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function reads a core file and marks it as read.
        """
        filename: str = kwargs.get("filename", "")

        if filename not in ALLOWED_FILES:
            return f"Error: '{filename}' is not a core file. Allowed: {', '.join(sorted(ALLOWED_FILES))}"

        path: str = os.path.join(self._workspace_dir, filename)
        if not os.path.isfile(path):
            return f"Error: {filename} does not exist."

        with open(file=path, mode="r") as f:
            content: str = f.read()

        # Mark this file as read for write authorization
        BaseTool._read_files.add(filename)

        return content


class WriteCoreFileTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the WriteCoreFileTool which writes the complete content of a core persona file.
        Requires that read_core_file was called first for the same file.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="write_core_file",
            description="Write the complete updated content to a core persona file. You MUST call read_core_file first for the same file. Provide the FULL file content, not a diff.",
            parameters={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "The core file to write (BEHAVIOR.md, SOUL.md, USER.md, or MEMORY.md)."
                    },
                    "content": {
                        "type": "string",
                        "description": "The complete new content for the file."
                    }
                },
                "required": ["filename", "content"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function writes a core file only if it was previously read.
        """
        filename: str = kwargs.get("filename", "")
        content: str = kwargs.get("content", "")

        if filename not in ALLOWED_FILES:
            return f"Error: '{filename}' is not a core file. Allowed: {', '.join(sorted(ALLOWED_FILES))}"

        # Enforce read-before-write
        if filename not in BaseTool._read_files:
            return f"Error: You must call read_core_file for '{filename}' before writing to it."

        path: str = os.path.join(self._workspace_dir, filename)
        with open(file=path, mode="w") as f:
            f.write(content)

        # Clear the read marker for this file
        BaseTool._read_files.discard(filename)

        return f"Successfully updated {filename}."

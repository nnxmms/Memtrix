#!/usr/bin/python3

import os
import re
from datetime import date
from typing import Any

from src.tools.base import BaseTool

# Pattern for valid memory filenames
DATE_PATTERN: re.Pattern = re.compile(pattern=r"^\d{4}-\d{2}-\d{2}\.md$")


class ReadMemoryFileTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the ReadMemoryFileTool which reads a daily memory file.
        """
        self._memory_dir: str = os.path.join(workspace_dir, "memory")
        os.makedirs(name=self._memory_dir, exist_ok=True)
        super().__init__(
            name="read_memory_file",
            description="Read a daily memory file from the memory/ directory. The filename must follow the pattern yyyy-mm-dd.md. If the file does not exist yet, returns empty content. Must be called before writing.",
            parameters={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "The memory file to read, e.g. 2026-03-18.md. Use get_current_time to find today's date if needed."
                    }
                },
                "required": ["filename"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function reads a daily memory file and marks it as read.
        """
        filename: str = kwargs.get("filename", "")

        if not DATE_PATTERN.match(string=filename):
            return f"Error: '{filename}' does not match the required pattern yyyy-mm-dd.md"

        path: str = os.path.join(self._memory_dir, filename)

        if not os.path.isfile(path):
            content: str = ""
        else:
            with open(file=path, mode="r") as f:
                content: str = f.read()

        # Mark this file as read for write authorization
        BaseTool._read_files.add(filename)

        return content if content else "(empty — this is a new memory file)"


class WriteMemoryFileTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the WriteMemoryFileTool which writes the complete content of a daily memory file.
        Requires that read_memory_file was called first for the same file.
        """
        self._memory_dir: str = os.path.join(workspace_dir, "memory")
        os.makedirs(name=self._memory_dir, exist_ok=True)
        super().__init__(
            name="write_memory_file",
            description="Write the complete updated content to a daily memory file. You MUST call read_memory_file first for the same file. Provide the FULL file content, not a diff.",
            parameters={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "The memory file to write, e.g. 2026-03-18.md."
                    },
                    "content": {
                        "type": "string",
                        "description": "The complete new content for the memory file."
                    }
                },
                "required": ["filename", "content"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function writes a daily memory file only if it was previously read.
        """
        filename: str = kwargs.get("filename", "")
        content: str = kwargs.get("content", "")

        if not DATE_PATTERN.match(string=filename):
            return f"Error: '{filename}' does not match the required pattern yyyy-mm-dd.md"

        # Enforce read-before-write
        if filename not in BaseTool._read_files:
            return f"Error: You must call read_memory_file for '{filename}' before writing to it."

        path: str = os.path.join(self._memory_dir, filename)
        with open(file=path, mode="w") as f:
            f.write(content)

        # Clear the read marker for this file
        BaseTool._read_files.discard(filename)

        return f"Successfully updated memory/{filename}."

#!/usr/bin/python3

import os
from datetime import date
from typing import Any

from src.tools.base import BaseTool


def _today_filename() -> str:
    """Returns today's memory filename in yyyy-mm-dd.md format."""
    return f"{date.today().isoformat()}.md"


class ReadMemoryFileTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the ReadMemoryFileTool which reads today's daily memory file.
        """
        self._memory_dir: str = os.path.join(workspace_dir, "memory")
        os.makedirs(name=self._memory_dir, exist_ok=True)
        super().__init__(
            name="read_memory_file",
            description="Read today's daily memory file. Always call this before writing.",
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function reads today's daily memory file and marks it as read.
        """
        filename: str = _today_filename()
        path: str = os.path.join(self._memory_dir, filename)

        if not os.path.isfile(path):
            content: str = ""
        else:
            with open(file=path, mode="r") as f:
                content: str = f.read()

        # Mark this file as read for write authorization
        room_id: str = kwargs.get("_room_id", "")
        BaseTool._read_files.setdefault(room_id, set()).add(filename)

        return content if content else "(empty — this is a new memory file)"


class WriteMemoryFileTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the WriteMemoryFileTool which writes the complete content of today's daily memory file.
        Requires that read_memory_file was called first.
        """
        self._workspace_dir: str = workspace_dir
        self._memory_dir: str = os.path.join(workspace_dir, "memory")
        os.makedirs(name=self._memory_dir, exist_ok=True)
        super().__init__(
            name="write_memory_file",
            description="Write the complete updated content to today's daily memory file. You MUST call read_memory_file first. Provide the FULL file content, not a diff.",
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The complete new content for today's memory file."
                    }
                },
                "required": ["content"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function writes today's daily memory file only if it was previously read.
        """
        content: str = kwargs.get("content", "")
        filename: str = _today_filename()

        # Enforce read-before-write
        room_id: str = kwargs.get("_room_id", "")
        if filename not in BaseTool._read_files.get(room_id, set()):
            return "Error: You must call read_memory_file before writing."

        path: str = os.path.join(self._memory_dir, filename)
        with open(file=path, mode="w") as f:
            f.write(content)

        # Clear the read marker for this file
        room_files: set[str] = BaseTool._read_files.get(room_id, set())
        room_files.discard(filename)

        return f"Successfully updated memory/{filename}."

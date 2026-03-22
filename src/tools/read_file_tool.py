#!/usr/bin/python3

import os
from typing import Any

import pymupdf

from src.tools.base import BaseTool

# Maximum characters to return from a file
MAX_CONTENT_LENGTH: int = 50000

# Files that must be accessed via dedicated tools (read_core_file / read_memory_file)
BLOCKED_FILES: set[str] = {"AGENT.md", "BEHAVIOR.md", "MEMORY.md", "SOUL.md", "USER.md"}
BLOCKED_DIRS: set[str] = {"memory"}


class ReadFileTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the ReadFileTool which reads files from the workspace.
        Automatically extracts text from PDFs. Core persona files and daily memory files
        are excluded — use the dedicated tools for those.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="read_file",
            description=(
                "Read the content of a file in the workspace. Supports text files and PDFs (extracted automatically). "
                "Cannot read core persona files (AGENT.md, BEHAVIOR.md, SOUL.md, USER.md, MEMORY.md) or daily memory files (memory/) — use the dedicated tools for those."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path relative to the workspace directory, e.g. attachments/report.pdf or notes.txt."
                    }
                },
                "required": ["path"]
            }
        )

    def _read_pdf(self, filepath: str) -> str:
        """
        This function extracts text from a PDF file using pymupdf.
        """
        try:
            doc: pymupdf.Document = pymupdf.open(filepath)
            text: str = ""
            for page in doc:
                text += page.get_text()
            doc.close()
        except Exception as e:
            return f"Error: failed to read PDF — {e}"

        text = text.strip()
        if not text:
            return "No readable text found in this PDF."

        return text

    def _read_text(self, filepath: str) -> str:
        """
        This function reads a plain text file.
        """
        try:
            with open(file=filepath, mode="r") as f:
                return f.read()
        except UnicodeDecodeError:
            return "Error: file is not a text file (binary content detected)."

    def execute(self, **kwargs: Any) -> str:
        """
        This function reads a file from the workspace, routing by extension.
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
            return f"Error: '{basename}' is a core file. Use read_core_file instead."

        # Block memory directory files
        relpath: str = os.path.relpath(filepath, self._workspace_dir)
        if relpath.startswith("memory" + os.sep) or relpath.startswith("memory/"):
            return "Error: memory files must be accessed via read_memory_file."

        if not os.path.isfile(path=filepath):
            return f"Error: file not found: {path}"

        # Route by file extension
        if filepath.lower().endswith(".pdf"):
            content: str = self._read_pdf(filepath=filepath)
        else:
            content: str = self._read_text(filepath=filepath)

        if not content:
            return "(empty file)"

        if len(content) > MAX_CONTENT_LENGTH:
            content = content[:MAX_CONTENT_LENGTH] + "\n\n[… content truncated]"

        return content

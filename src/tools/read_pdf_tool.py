#!/usr/bin/python3

import os
from typing import Any

import pymupdf

from src.tools.base import BaseTool

MAX_CONTENT_LENGTH: int = 50000


class ReadPDFTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the ReadPDFTool which extracts text from PDF files in the workspace.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="read_pdf",
            description="Extract text content from a PDF file in the workspace (including attachments/).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path relative to the workspace directory, e.g. attachments/document.pdf."
                    }
                },
                "required": ["path"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function extracts text from a PDF file.
        """
        path: str = kwargs.get("path", "")
        if not path:
            return "Error: path cannot be empty."

        filepath: str = os.path.join(self._workspace_dir, path)

        # Prevent path traversal
        if not os.path.realpath(filepath).startswith(os.path.realpath(self._workspace_dir)):
            return "Error: path must be within the workspace directory."

        if not os.path.isfile(path=filepath):
            return f"Error: file not found: {path}"

        if not filepath.lower().endswith(".pdf"):
            return "Error: file must be a PDF."

        try:
            doc: pymupdf.Document = pymupdf.open(filepath)
            text: str = ""
            for page in doc:
                text += page.get_text()
            doc.close()
        except Exception as e:
            return f"Error: failed to read PDF — {e}"

        text: str | Any = text.strip()
        if not text:
            return "No readable text found in this PDF."

        if len(text) > MAX_CONTENT_LENGTH:
            text: str | Any = text[:MAX_CONTENT_LENGTH] + "\n\n[… content truncated]"

        return text

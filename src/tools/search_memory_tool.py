#!/usr/bin/python3

from typing import Any

from src.memory_index import MemoryIndex
from src.tools.base import BaseTool


class SearchMemoryTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the SearchMemoryTool which searches daily memory files by semantic similarity.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="search_memory",
            description="Search your daily memory files for past conversations, facts, or events. Returns matching dates and snippets. Use read_memory_file afterwards to get the full content of a specific day.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for in your memories, e.g. 'cake recipe' or 'user's birthday'."
                    }
                },
                "required": ["query"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function searches the memory index and returns formatted results.
        """
        query: str = kwargs.get("query", "")
        if not query:
            return "Error: search query cannot be empty."

        index: MemoryIndex = MemoryIndex.get_instance(workspace_dir=self._workspace_dir)
        matches: list[dict[str, Any]] = index.search(query=query)

        if not matches:
            return "No matching memories found."

        lines: list[str] = []
        for match in matches:
            lines.append(f"**{match['date']}** ({match['filename']})\n{match['snippet']}")

        return "\n\n---\n\n".join(lines)

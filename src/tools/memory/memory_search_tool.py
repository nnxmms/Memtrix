#!/usr/bin/python3

from typing import Any

from src.memory.store import RepresentationStore
from src.tools.base import BaseTool


class MemorySearchTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the MemorySearchTool which semantically searches the reasoned
        conclusions about the user and returns ranked excerpts.
        """
        self._workspace_dir: str = workspace_dir
        self._store: RepresentationStore | None = None
        super().__init__(
            name="memory_search",
            description="Search your reasoned memory (durable conclusions about the user) and return the most relevant excerpts. Use for 'what do you know about...' style recall.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to recall, e.g. 'user's preferences'."
                    }
                },
                "required": ["query"]
            }
        )

    def set_representation(self, store: RepresentationStore) -> None:
        """
        This function injects the representation store dependency.
        """
        self._store = store

    def execute(self, **kwargs: Any) -> str:
        """
        This function returns ranked conclusion excerpts for a query.
        """
        if self._store is None:
            return "Memory search is not available."

        query: str = kwargs.get("query", "")
        if not query:
            return "Error: search query cannot be empty."

        matches: list[dict[str, Any]] = self._store.search(query=query, peer="user", n_results=8)
        if not matches:
            return "No relevant memories found."

        lines: list[str] = []
        for match in matches:
            lines.append(f"- ({match['kind']}) {match['content']}")
        return "\n".join(lines)

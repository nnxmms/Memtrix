#!/usr/bin/python3

from typing import Any

from src.indexing.docs import DocsIndex
from src.tools.base import BaseTool


class SearchDocsTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the SearchDocsTool which searches the Memtrix documentation by
        semantic similarity and returns matching sections with citations.
        """
        self._workspace_dir: str = workspace_dir
        self._index: DocsIndex | None = None
        super().__init__(
            name="search_docs",
            description="Search the Memtrix documentation for how the system works (setup, configuration, memory, tools, agents, security, Docker, etc.). Returns matching sections with citations. Use ask_docs instead when you want a synthesized answer rather than raw excerpts.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to look up in the docs, e.g. 'how does reasoning memory work' or 'configure an OpenRouter provider'."
                    }
                },
                "required": ["query"]
            }
        )

    def set_docs_index(self, index: DocsIndex) -> None:
        """
        This function injects the documentation index dependency.
        """
        self._index = index

    def execute(self, **kwargs: Any) -> str:
        """
        This function searches the documentation index and returns formatted results.
        """
        query: str = kwargs.get("query", "")
        if not query:
            return "Error: search query cannot be empty."

        index: DocsIndex = self._index or DocsIndex.get_instance()
        matches: list[dict[str, Any]] = index.search(query=query)

        if not matches:
            return "No matching documentation found."

        lines: list[str] = []
        for match in matches:
            heading: str = match["section_title"] or match["page_title"]
            lines.append(f"**{heading}** ({match['anchor']})\n{match['snippet']}")

        return "\n\n---\n\n".join(lines)

#!/usr/bin/python3

from typing import Any

from src.memory.index import ConversationIndex
from src.tools.base import BaseTool


class SearchMemoryTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the SearchMemoryTool which semantically searches past conversations.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="search_memory",
            description="Search your past conversations with the user by meaning. Use this to recall something discussed days or weeks ago — a tool, a project, a decision, a name. Returns the date and a transcript excerpt from the most relevant past conversations.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to recall, in natural language, e.g. 'the database tool we discussed' or 'the user's travel plans'."
                    }
                },
                "required": ["query"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function searches the conversation index and returns formatted excerpts.
        """
        query: str = kwargs.get("query", "")
        if not query:
            return "Error: search query cannot be empty."

        index: ConversationIndex = ConversationIndex.get_instance(workspace_dir=self._workspace_dir)
        matches: list[dict[str, Any]] = index.search(query=query)

        if not matches:
            return "No matching conversations found."

        lines: list[str] = [
            f"**{match['date']}**\n{match['snippet']}"
            for match in matches
        ]

        return "\n\n---\n\n".join(lines)

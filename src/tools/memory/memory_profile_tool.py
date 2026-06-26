#!/usr/bin/python3

from typing import Any

from src.memory.store import RepresentationStore
from src.tools.base import BaseTool


class MemoryProfileTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the MemoryProfileTool which returns the finite user profile card
        (compact, always-current facts about the user). No LLM call.
        """
        self._workspace_dir: str = workspace_dir
        self._store: RepresentationStore | None = None
        super().__init__(
            name="memory_profile",
            description="Get the compact profile card: durable key facts about the user. Fast, no search. Use this to ground yourself on who you're talking to.",
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )

    def set_representation(self, store: RepresentationStore) -> None:
        """
        This function injects the representation store dependency.
        """
        self._store = store

    def execute(self, **kwargs: Any) -> str:
        """
        This function returns the user profile card.
        """
        if self._store is None:
            return "Memory profile is not available."

        user_card: str = self._store.read_peer_card(peer="user")
        return f"**User profile:**\n{user_card or '(nothing recorded yet)'}"

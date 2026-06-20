#!/usr/bin/python3

from typing import Any

from src.memory.store import RepresentationStore
from src.tools.base import BaseTool


class MemoryProfileTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the MemoryProfileTool which returns the finite peer cards (compact,
        always-current facts about the user and the agent itself). No LLM call.
        """
        self._workspace_dir: str = workspace_dir
        self._store: RepresentationStore | None = None
        super().__init__(
            name="memory_profile",
            description="Get the compact profile cards: durable key facts about the user and about yourself. Fast, no search. Use this to ground yourself on who you're talking to.",
            parameters={
                "type": "object",
                "properties": {
                    "peer": {
                        "type": "string",
                        "enum": ["user", "agent", "both"],
                        "description": "Whose profile to return. Defaults to 'both'."
                    }
                },
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
        This function returns the requested peer card(s).
        """
        if self._store is None:
            return "Memory profile is not available."

        peer: str = kwargs.get("peer", "both")
        sections: list[str] = []
        if peer in ("user", "both"):
            user_card: str = self._store.read_peer_card(peer="user")
            sections.append(f"**User profile:**\n{user_card or '(nothing recorded yet)'}")
        if peer in ("agent", "both"):
            agent_card: str = self._store.read_peer_card(peer="agent")
            sections.append(f"**My profile:**\n{agent_card or '(nothing recorded yet)'}")

        return "\n\n".join(sections) if sections else "No profile available."

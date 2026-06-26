#!/usr/bin/python3

from typing import Any

from src.memory.store import RepresentationStore, slugify
from src.tools.base import BaseTool


class MemoryProfileTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the MemoryProfileTool which returns the finite user profile card
        (compact, always-current facts about the user), or — when a name is given —
        the profile card of a person/project/place the agent has learned about. No
        LLM call.
        """
        self._workspace_dir: str = workspace_dir
        self._store: RepresentationStore | None = None
        super().__init__(
            name="memory_profile",
            description="Get a compact profile card. With no arguments, returns durable key facts about the user (fast, no search) to ground yourself on who you're talking to. Pass 'name' to instead get what you know about a specific person, project, or place the user has mentioned.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Optional. The name of a person, project, or place to look up. Omit to get the user's own profile card.",
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
        This function returns the user profile card, or a named entity's card.
        """
        if self._store is None:
            return "Memory profile is not available."

        name: str = (kwargs.get("name") or "").strip()
        if name:
            slug: str = slugify(name)
            card: str = self._store.read_entity_card(slug=slug)
            if card:
                return f"**Profile — {name}:**\n{card}"
            facts: list[dict[str, Any]] = self._store.all_for_peer(peer="user", limit=20, entity=slug)
            if facts:
                lines: str = "\n".join(f"- {f['content']}" for f in facts)
                return f"**What I know about {name}:**\n{lines}"
            return f"I don't have anything recorded about {name} yet."

        user_card: str = self._store.read_peer_card(peer="user")
        return f"**User profile:**\n{user_card or '(nothing recorded yet)'}"


#!/usr/bin/python3

from typing import Any

from src.memory.store import RepresentationStore
from src.tools.base import BaseTool


class MemoryConcludeTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the MemoryConcludeTool which immediately stores a durable fact when the
        user states a preference, correction, or important piece of context.
        """
        self._workspace_dir: str = workspace_dir
        self._store: RepresentationStore | None = None
        super().__init__(
            name="memory_conclude",
            description="Immediately store a durable fact in reasoned memory when the user states a preference, correction, or important context you should remember long-term. Use sparingly for high-signal facts.",
            parameters={
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "The durable fact to remember, phrased as a standalone statement, e.g. 'The user prefers concise answers.'"
                    },
                    "peer": {
                        "type": "string",
                        "enum": ["user", "agent"],
                        "description": "Whether this fact is about the user or about yourself. Defaults to 'user'."
                    }
                },
                "required": ["fact"]
            }
        )

    def set_representation(self, store: RepresentationStore) -> None:
        """
        This function injects the representation store dependency.
        """
        self._store = store

    def execute(self, **kwargs: Any) -> str:
        """
        This function stores a single durable fact as an operator-locked conclusion so
        the background consolidation pass never prunes or rewrites it.
        """
        if self._store is None:
            return "Memory is not available."

        fact: str = (kwargs.get("fact") or "").strip()
        if not fact:
            return "Error: fact cannot be empty."

        peer: str = kwargs.get("peer", "user")
        if peer not in ("user", "agent"):
            peer = "user"

        record_id: str | None = self._store.add_manual_conclusion(
            peer=peer,
            kind="deductive",
            content=fact,
            premises=["explicitly committed to memory"],
            confidence="high",
        )
        return "Noted." if record_id else "Could not store that."

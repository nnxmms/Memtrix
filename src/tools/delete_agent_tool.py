#!/usr/bin/python3

from typing import Any

from src.tools.base import BaseTool
from src.tools.utils import confirm_with_user


class DeleteAgentTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the DeleteAgentTool which deletes a sub-agent.
        """
        self._workspace_dir: str = workspace_dir
        self._agent_manager: Any = None
        super().__init__(
            name="delete_agent",
            description=(
                "Delete a sub-agent. This permanently removes the agent's workspace, memory, sessions, and config. "
                "The Matrix user account remains on the homeserver but will no longer respond to messages."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The name of the agent to delete (e.g. 'Dennis', 'Jenny')."
                    }
                },
                "required": ["name"]
            }
        )

    def set_agent_manager(self, manager: Any) -> None:
        """
        This function sets the agent manager reference.
        """
        self._agent_manager = manager

    def execute(self, **kwargs: Any) -> str:
        """
        This function deletes a sub-agent.
        """
        if not self._agent_manager:
            return "Error: agent manager not initialized."

        name: str = kwargs.get("name", "").strip()
        if not name:
            return "Error: name cannot be empty."

        # Human-in-the-loop: confirm deletion
        confirm_msg: str = (
            f"Permanently delete sub-agent '{name}'?\n\n"
            f"This will remove the agent's workspace, all memory files, sessions, and vector index.\n"
            f"This action cannot be undone.\n\n"
            f"Allow? (yes/no)"
        )
        if not confirm_with_user(kwargs, message=confirm_msg):
            return "Agent deletion denied by user."

        return self._agent_manager.delete_agent(name=name)

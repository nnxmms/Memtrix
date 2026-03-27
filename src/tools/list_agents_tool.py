#!/usr/bin/python3

from typing import Any

from src.tools.base import BaseTool


class ListAgentsTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the ListAgentsTool which lists all registered sub-agents.
        """
        self._workspace_dir: str = workspace_dir
        self._agent_manager: Any = None
        super().__init__(
            name="list_agents",
            description=(
                "List all registered sub-agents with their name, description, Matrix user ID, model, and status."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )

    def set_agent_manager(self, manager: Any) -> None:
        """
        This function sets the agent manager reference.
        """
        self._agent_manager = manager

    def execute(self, **kwargs: Any) -> str:
        """
        This function lists all sub-agents.
        """
        if not self._agent_manager:
            return "Error: agent manager not initialized."

        return self._agent_manager.list_agents()

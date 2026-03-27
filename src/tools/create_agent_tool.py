#!/usr/bin/python3

from typing import Any

from src.tools.base import BaseTool
from src.tools.utils import confirm_with_user


class CreateAgentTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the CreateAgentTool which creates a new sub-agent.
        """
        self._workspace_dir: str = workspace_dir
        self._agent_manager: Any = None
        super().__init__(
            name="create_agent",
            description=(
                "Create a new specialist sub-agent with its own Matrix identity, workspace, memory, and persona. "
                "The agent will be available as a separate Matrix user that the human can invite to rooms. "
                "Requires a real human name for the agent and a description of its expertise. "
                "You MUST ask the user for a name if they haven't provided one."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "A real human name for the agent (e.g. 'Dennis', 'Jenny', 'Marco'). This becomes the agent's identity."
                    },
                    "description": {
                        "type": "string",
                        "description": "A clear description of the agent's area of expertise. E.g. 'Baking and pastry specialist — recipes, techniques, troubleshooting'"
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional model instance name from config. Defaults to the main agent's model."
                    }
                },
                "required": ["name", "description"]
            }
        )

    def set_agent_manager(self, manager: Any) -> None:
        """
        This function sets the agent manager reference.
        """
        self._agent_manager = manager

    def execute(self, **kwargs: Any) -> str:
        """
        This function creates a new sub-agent.
        """
        if not self._agent_manager:
            return "Error: agent manager not initialized."

        name: str = kwargs.get("name", "").strip()
        description: str = kwargs.get("description", "").strip()
        model: str = kwargs.get("model", "").strip()

        if not name:
            return "Error: name cannot be empty. Ask the user what they want to name this agent."
        if not description:
            return "Error: description cannot be empty."

        # Human-in-the-loop: confirm agent creation
        confirm_msg: str = (
            f"Create a new sub-agent?\n\n"
            f"  Name: {name}\n"
            f"  Expertise: {description}\n\n"
            f"This will register a new Matrix user and create a workspace.\n"
            f"Allow? (yes/no)"
        )
        if not confirm_with_user(kwargs, message=confirm_msg):
            return "Agent creation denied by user."

        return self._agent_manager.create_agent(
            name=name,
            description=description,
            model=model
        )

#!/usr/bin/python3

from typing import Any

from src.tools.base import BaseTool


class AskAgentTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the AskAgentTool which allows agents to consult each other.
        """
        self._workspace_dir: str = workspace_dir
        self._agent_manager: Any = None
        self._caller_name: str = ""
        super().__init__(
            name="ask_agent",
            description=(
                "Ask another agent a question and get their response. "
                "Use this when a question falls in another agent's area of expertise. "
                "Frame your question with enough context for them to give a useful answer. "
                "The other agent has full access to their own memory and tools."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The name of the agent to ask (e.g. 'Dennis', 'Jenny')."
                    },
                    "message": {
                        "type": "string",
                        "description": "The question or message to send to the agent. Include enough context for a useful answer."
                    }
                },
                "required": ["name", "message"]
            }
        )

    def set_agent_manager(self, manager: Any) -> None:
        """
        This function sets the agent manager reference.
        """
        self._agent_manager = manager

    def set_caller_name(self, name: str) -> None:
        """
        This function sets the name of the agent that owns this tool instance.
        """
        self._caller_name = name

    def execute(self, **kwargs: Any) -> str:
        """
        This function sends a message to another agent and returns their response.
        """
        if not self._agent_manager:
            return "Error: agent manager not initialized."

        target_name: str = kwargs.get("name", "").strip()
        message: str = kwargs.get("message", "").strip()

        if not target_name:
            return "Error: name cannot be empty."
        if not message:
            return "Error: message cannot be empty."

        # Get current call depth from kwargs (injected by the orchestrator)
        depth: int = kwargs.get("_agent_depth", 0)

        return self._agent_manager.query_agent(
            caller_name=self._caller_name,
            target_name=target_name,
            message=message,
            depth=depth
        )

#!/usr/bin/python3

from typing import Any


class BaseTool:

    # Shared tracker for read-before-write enforcement on core files
    _read_files: set[str] = set()

    def __init__(self, name: str, description: str, parameters: dict[str, Any]) -> None:
        """
        This is the BaseTool class which all tools inherit from.
        """
        # Tool name (used by the LLM to call this tool)
        self.name: str = name

        # Human-readable description of what this tool does
        self.description: str = description

        # JSON Schema describing the tool's parameters
        self.parameters: dict[str, Any] = parameters

    def execute(self, **kwargs: Any) -> str:
        """
        This function executes the tool and returns a string result.
        """
        raise NotImplementedError

    def schema(self) -> dict[str, Any]:
        """
        This function returns the tool definition in OpenAI-compatible format.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }

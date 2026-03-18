#!/usr/bin/python3

from typing import Any


class BaseProvider:

    def __init__(self, name: str) -> None:
        """
        This is the BaseProvider class which all other provider classes inherit from.
        """
        # LLM provider
        self.name: str = name

    def completions(self, model: str, history: list[dict], tools: list[dict] | None = None, think: bool = False) -> Any:
        """
        This function takes the chat history and returns the provider's message response.
        The returned object must have .content (str | None) and .tool_calls (list | None).
        """
        raise NotImplementedError
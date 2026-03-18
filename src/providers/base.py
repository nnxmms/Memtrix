#!/usr/bin/python3

class BaseProvider:

    def __init__(self, name: str) -> None:
        """
        This is the BaseProvider class which all other provider classes inherit from.
        """
        # LLM provider
        self.name: str = name

    def completions(self, history: list[dict[str, str]]) -> str:
        """
        This function takes the chat history and returns the provider's response.
        """
        raise NotImplementedError
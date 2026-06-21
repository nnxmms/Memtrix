#!/usr/bin/python3

from typing import Any


class BaseProvider:

    # Multimodal message style used when expanding image attachments. "openai" emits a
    # content list with image_url data URLs; "ollama" attaches a native images=[b64] key.
    image_style: str = "openai"

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
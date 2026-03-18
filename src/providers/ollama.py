#!/usr/bin/python3

from typing import Any

from ollama import Client

from src.providers.base import BaseProvider


class OllamaProvider(BaseProvider):

    def __init__(self, base_url: str) -> None:
        """
        This is the OllamaProvider class which provides Ollama LLM support.
        """
        super().__init__(name="ollama")

        # Ollama base url
        self.base_url: str = base_url

        # Ollama client
        self._client: Client = Client(host=self.base_url)

    def completions(self, model: str, history: list[dict[str, str]]) -> str:
        """
        This function takes the chat history and returns the model's response.
        """
        response: Any = self._client.chat(model=model, messages=history)
        return response.message.content

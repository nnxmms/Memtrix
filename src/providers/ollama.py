#!/usr/bin/python3

import logging
from typing import Any

from ollama import Client

from src.providers.base import BaseProvider

logger: logging.Logger = logging.getLogger(__name__)


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

    def completions(self, model: str, history: list[dict], tools: list[dict] | None = None, think: bool = False) -> Any:
        """
        This function takes the chat history and returns the model's message response.
        """
        # Build kwargs, only include tools if provided
        kwargs: dict[str, Any] = {"model": model, "messages": history}
        if tools:
            kwargs["tools"] = tools
        if think:
            kwargs["think"] = True

        response: Any = self._client.chat(**kwargs)
        logger.debug("Ollama response received (model=%s)", model)
        return response.message

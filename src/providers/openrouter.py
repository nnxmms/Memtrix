#!/usr/bin/python3

import json
from typing import Any

from openai import OpenAI

from src.providers.base import BaseProvider


class _ToolFunction:
    """Wraps a tool call function to provide arguments as a dict."""

    def __init__(self, name: str, arguments: dict[str, Any]) -> None:
        self.name: str = name
        self.arguments: dict[str, Any] = arguments


class _ToolCall:
    """Wraps a tool call to match the expected interface."""

    def __init__(self, function: _ToolFunction) -> None:
        self.function: _ToolFunction = function


class _Message:
    """Wraps an OpenAI message to match the Ollama message interface."""

    def __init__(self, content: str | None, tool_calls: list[_ToolCall] | None, thinking: str | None = None) -> None:
        self.content: str | None = content
        self.tool_calls: list[_ToolCall] | None = tool_calls
        self.thinking: str | None = thinking


class OpenRouterProvider(BaseProvider):

    def __init__(self, api_key: str) -> None:
        """
        This is the OpenRouterProvider class which provides OpenRouter LLM support.
        """
        super().__init__(name="openrouter")

        # OpenAI-compatible client pointed at OpenRouter
        self._client: OpenAI = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key
        )

    def completions(self, model: str, history: list[dict], tools: list[dict] | None = None, think: bool = False) -> _Message:
        """
        This function takes the chat history and returns the model's message response.
        """
        kwargs: dict[str, Any] = {"model": model, "messages": history}
        if tools:
            kwargs["tools"] = tools

        response: Any = self._client.chat.completions.create(**kwargs)
        message: Any = response.choices[0].message

        # Parse tool call arguments from JSON strings to dicts
        wrapped_tool_calls: list[_ToolCall] | None = None
        if message.tool_calls:
            wrapped_tool_calls = [
                _ToolCall(function=_ToolFunction(
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments
                ))
                for tc in message.tool_calls
            ]

        return _Message(
            content=message.content,
            tool_calls=wrapped_tool_calls
        )

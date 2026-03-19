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

    def __init__(self, function: _ToolFunction, id: str | None = None) -> None:
        self.function: _ToolFunction = function
        self.id: str | None = id


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

    @staticmethod
    def _sanitize_history(history: list[dict]) -> list[dict]:
        """
        This function ensures the message history conforms to OpenAI's strict format.
        Ollama stores tool call arguments as dicts; OpenAI requires JSON strings.
        """
        sanitized: list[dict[str, Any]] = []
        for msg in history:
            if msg.get("tool_calls"):
                msg = dict(msg)
                msg["tool_calls"] = [
                    {
                        "id": tc.get("id", f"call_{i}"),
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": json.dumps(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], dict) else tc["function"]["arguments"]
                        }
                    }
                    for i, tc in enumerate(msg["tool_calls"])
                ]
            sanitized.append(msg)
        return sanitized

    @staticmethod
    def _sanitize_tools(tools: list[dict]) -> list[dict]:
        """
        This function ensures tool schemas are strictly OpenAI-compatible.
        Some providers behind OpenRouter are stricter about the format than Ollama.
        """
        sanitized: list[dict[str, Any]] = []
        for tool in tools:
            func: dict[str, Any] = dict(tool.get("function", {}))
            params: dict[str, Any] = func.get("parameters", {})
            # Drop empty parameters entirely — OpenAI treats missing as no-args
            if not params.get("properties"):
                func.pop("parameters", None)
            sanitized.append({"type": "function", "function": func})
        return sanitized

    def completions(self, model: str, history: list[dict], tools: list[dict] | None = None, think: bool = False) -> _Message:
        """
        This function takes the chat history and returns the model's message response.
        """
        kwargs: dict[str, Any] = {"model": model, "messages": self._sanitize_history(history=history)}
        if tools:
            kwargs["tools"] = self._sanitize_tools(tools=tools)
        if think:
            kwargs["extra_body"] = {"include_reasoning": True}

        response: Any = self._client.chat.completions.create(**kwargs)
        message: Any = response.choices[0].message

        # Extract reasoning content (OpenRouter returns it differently than Ollama)
        thinking: str | None = getattr(message, "reasoning", None) or getattr(message, "reasoning_content", None)

        # Parse tool call arguments from JSON strings to dicts
        wrapped_tool_calls: list[_ToolCall] | None = None
        if message.tool_calls:
            wrapped_tool_calls = [
                _ToolCall(
                    id=tc.id,
                    function=_ToolFunction(
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments
                    )
                )
                for tc in message.tool_calls
            ]

        return _Message(
            content=message.content,
            tool_calls=wrapped_tool_calls,
            thinking=thinking
        )

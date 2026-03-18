#!/usr/bin/python3

import json
import os
import re
from typing import Any, Callable

from src.providers.base import BaseProvider
from src.session import Session
from src.tools.base import BaseTool

# Maximum number of tool-call rounds per request
MAX_ITERATIONS: int = 10


class Orchestrator:

    def __init__(self, provider: BaseProvider, model: str, tools: list[BaseTool], workspace_dir: str, think: bool = False) -> None:
        """
        This is the Orchestrator class which runs the agentic tool-calling loop.
        """
        # LLM provider and model
        self._provider: BaseProvider = provider
        self._model: str = model
        self._think: bool = think

        # Tool registry keyed by name
        self._tools: dict[str, BaseTool] = {tool.name: tool for tool in tools}

        # Tool schemas for the LLM
        self._tool_schemas: list[dict[str, Any]] = [tool.schema() for tool in tools]

        # System prompt built from workspace files
        self._workspace_dir: str = workspace_dir
        self._system_prompt: str = self._build_system_prompt(workspace_dir=workspace_dir)

        # Optional callback for verbose/status messages
        self._notify: Callable[[str], None] | None = None

        # Optional callback for reasoning display
        self._notify_reasoning: Callable[[str], None] | None = None

    def set_notify(self, callback: Callable[[str], None]) -> None:
        """
        This function sets the callback for sending verbose notifications.
        """
        self._notify = callback

    def set_notify_reasoning(self, callback: Callable[[str], None] | None) -> None:
        """
        This function sets the callback for sending reasoning notifications.
        """
        self._notify_reasoning = callback

    def _emit_reasoning(self, message: Any) -> None:
        """
        This function emits the model's reasoning content if available and callback is set.
        """
        if self._notify_reasoning:
            thinking: str = getattr(message, "thinking", None) or ""
            if thinking.strip():
                self._notify_reasoning(f"💭 {thinking.strip()}")

    def _emit(self, message: str) -> None:
        """
        This function sends a verbose notification if a callback is set.
        """
        if self._notify:
            self._notify(message)

    def _build_system_prompt(self, workspace_dir: str) -> str:
        """
        This function builds the system prompt by reading AGENT.md and injecting
        the contents of SOUL.md, USER.md, and MEMORY.md into its placeholders.
        """
        # Read AGENT.md as the template
        agent_path: str = os.path.join(workspace_dir, "AGENT.md")
        if not os.path.isfile(path=agent_path):
            return "You are Memtrix, a helpful personal AI assistant."

        with open(file=agent_path, mode="r") as f:
            template: str = f.read()

        # Map placeholders to their source files
        placeholders: dict[str, str] = {
            "{{BEHAVIOR}}": "BEHAVIOR.md",
            "{{SOUL}}": "SOUL.md",
            "{{USER}}": "USER.md",
            "{{MEMORY}}": "MEMORY.md"
        }

        # Replace each placeholder with the file's content
        for placeholder, filename in placeholders.items():
            path: str = os.path.join(workspace_dir, filename)
            content: str = ""
            if os.path.isfile(path):
                with open(file=path, mode="r") as f:
                    content: str = f.read().strip()
            template: str = template.replace(placeholder, content or "(not set)")

        return template

    def _serialize_message(self, message: Any) -> dict[str, Any]:
        """
        This function converts a provider message object to a serializable dict.
        """
        result: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            result["tool_calls"] = [
                {
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                }
                for tc in message.tool_calls
            ]
        return result

    def run(self, user_message: str, session: Session) -> str:
        """
        This function processes a user message through the agentic loop and returns the final response.
        """
        # Reset read-before-write tracker for core file tools
        BaseTool._read_files.clear()

        # Inject system prompt at the start of a fresh session
        if not session.history:
            session.append(message={"role": "system", "content": self._system_prompt})

        # Add the user message to the session
        session.append(message={"role": "user", "content": user_message})

        # Agentic loop — call LLM, execute tools, repeat
        for _ in range(MAX_ITERATIONS):
            # Call the LLM with the full session history and tool definitions
            message: Any = self._provider.completions(
                model=self._model,
                history=session.history,
                tools=self._tool_schemas,
                think=self._think
            )

            # Emit reasoning if available
            self._emit_reasoning(message=message)

            # If no tool calls, save and return the final text response
            if not message.tool_calls:
                response: str = self._strip_thinking(text=message.content or "")
                session.append(message={"role": "assistant", "content": response})
                return response

            # Save the assistant's tool-call message to session
            session.append(message=self._serialize_message(message=message))

            # Execute each requested tool and save results
            for tool_call in message.tool_calls:
                tool_name: str = tool_call.function.name
                tool_args: dict[str, Any] = tool_call.function.arguments

                # Notify about the tool call
                args_summary: str = ", ".join(f"{k}={v}" for k, v in tool_args.items() if k != "content")
                self._emit(message=f"→ Tool call: {tool_name}({args_summary})")

                # Execute the tool or report an error
                if tool_name in self._tools:
                    try:
                        result: str = self._tools[tool_name].execute(**tool_args)
                    except Exception as e:
                        result: str = f"Error: {e}"
                else:
                    result: str = f"Error: unknown tool '{tool_name}'"

                # Notify about the tool response
                self._emit(message=f"→ Tool response received")

                session.append(message={"role": "tool", "content": result})

                # Rebuild system prompt if a core file was updated
                if tool_name == "write_core_file" and result.startswith("Successfully"):
                    self._system_prompt = self._build_system_prompt(workspace_dir=self._workspace_dir)

        # Exhausted iterations — ask for a final answer without tools
        session.append(message={"role": "user", "content": "Please provide your final answer now."})
        message = self._provider.completions(model=self._model, history=session.history, think=self._think)
        self._emit_reasoning(message=message)
        response: str = self._strip_thinking(text=message.content or "")
        session.append(message={"role": "assistant", "content": response})
        return response

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """
        This function removes leaked <think>...</think> tags from model output.
        """
        cleaned: str = re.sub(pattern=r"<think>.*?</think>", repl="", string=text, flags=re.DOTALL)
        cleaned = re.sub(pattern=r"</think>", repl="", string=cleaned)
        return cleaned.strip()

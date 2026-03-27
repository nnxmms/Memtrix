#!/usr/bin/python3

import json
import logging
import os
import re
from typing import Any, Callable

from src.providers.base import BaseProvider
from src.session import Session
from src.tools.base import BaseTool

logger: logging.Logger = logging.getLogger(__name__)

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
            serialized_calls: list[dict[str, Any]] = []
            for tc in message.tool_calls:
                tc_dict: dict[str, Any] = {
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                }
                if getattr(tc, "id", None):
                    tc_dict["id"] = tc.id
                serialized_calls.append(tc_dict)
            result["tool_calls"] = serialized_calls
        return result

    def run(self, user_message: str, session: Session, room_id: str = "",
            notify: Callable[[str], None] | None = None,
            notify_reasoning: Callable[[str], None] | None = None,
            send_file: Callable[[str], None] | None = None,
            ask: Callable[[str], str] | None = None,
            react: Callable[[str], None] | None = None,
            agent_depth: int = 0) -> str:
        """
        This function processes a user message through the agentic loop and returns the final response.
        All callbacks are per-call parameters — the orchestrator holds no mutable per-request state.
        """
        # Reset read-before-write tracker for this room
        BaseTool._read_files.pop(room_id, None)

        # Propagate send_file callback to the send_file tool for this request
        if "send_file" in self._tools:
            self._tools["send_file"].set_send_file(callback=send_file)

        # Inject system prompt at the start of a fresh session
        if not session.history:
            session.append(message={"role": "system", "content": self._system_prompt})

        # Add the user message to the session
        session.append(message={"role": "user", "content": user_message})

        # Agentic loop — call LLM, execute tools, repeat
        for iteration in range(MAX_ITERATIONS):
            # Call the LLM with the full session history and tool definitions
            logger.debug("LLM call (iteration %d, room=%s)", iteration + 1, room_id)
            message: Any = self._provider.completions(
                model=self._model,
                history=session.history,
                tools=self._tool_schemas,
                think=self._think
            )

            # Emit reasoning if available
            if notify_reasoning:
                thinking: str = getattr(message, "thinking", None) or ""
                if thinking.strip():
                    notify_reasoning(f"💭 {thinking.strip()}")

            # If no tool calls, save and return the final text response
            if not message.tool_calls:
                response: str = self._strip_thinking(text=message.content or "")
                session.append(message={"role": "assistant", "content": response})
                logger.info("Response ready (room=%s, length=%d)", room_id, len(response))
                return response

            # Save the assistant's tool-call message to session
            session.append(message=self._serialize_message(message=message))

            # Execute each requested tool and save results
            for tool_call in message.tool_calls:
                tool_name: str = tool_call.function.name
                tool_args: dict[str, Any] = tool_call.function.arguments

                # Notify about the tool call
                args_summary: str = ", ".join(f"{k}={v}" for k, v in tool_args.items() if k != "content")
                if notify:
                    notify(f"→ Tool call: {tool_name}({args_summary})")

                # Execute the tool or report an error
                if tool_name in self._tools:
                    try:
                        logger.info("Executing tool '%s' (room=%s)", tool_name, room_id)
                        exec_args: dict[str, Any] = {
                            **tool_args,
                            "_room_id": room_id,
                            "_ask": ask,
                            "_react": react,
                            "_agent_depth": agent_depth
                        }
                        result: str = self._tools[tool_name].execute(**exec_args)
                    except Exception as e:
                        result: str = f"Error: {e}"
                        logger.error("Tool '%s' raised an exception: %s", tool_name, e)
                else:
                    result: str = f"Error: unknown tool '{tool_name}'"
                    logger.warning("LLM requested unknown tool '%s'", tool_name)

                # Notify about the tool response
                if notify:
                    notify(f"→ Tool response received")

                tool_result: dict[str, str] = {"role": "tool", "content": result}
                if getattr(tool_call, "id", None):
                    tool_result["tool_call_id"] = tool_call.id
                session.append(message=tool_result)

                # Rebuild system prompt if a core file was updated
                if tool_name == "write_core_file" and result.startswith("Successfully"):
                    self._system_prompt = self._build_system_prompt(workspace_dir=self._workspace_dir)

        # Exhausted iterations — ask for a final answer without tools
        logger.warning("Exhausted %d iterations (room=%s), forcing final answer", MAX_ITERATIONS, room_id)
        session.append(message={"role": "user", "content": "Please provide your final answer now."})
        message = self._provider.completions(model=self._model, history=session.history, think=self._think)
        if notify_reasoning:
            thinking: str = getattr(message, "thinking", None) or ""
            if thinking.strip():
                notify_reasoning(f"💭 {thinking.strip()}")
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

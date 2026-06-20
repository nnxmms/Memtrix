#!/usr/bin/python3

import logging
import os
import re
from typing import Any, Callable

from src.memory.deriver import Deriver
from src.providers.base import BaseProvider
from src.memory.store import RepresentationStore
from src.core.session import Session
from src.tools.base import BaseTool

logger: logging.Logger = logging.getLogger(__name__)

# Default number of tool-call rounds per request (overridable via the "agent" config block)
DEFAULT_MAX_ITERATIONS: int = 25

# Prefix marking a transient recall block injected into the working history
RECALL_PREFIX: str = "📎 Relevant things I recall"

# Prefix marking a transient skill-catalog block injected into the working history
SKILL_PREFIX: str = "🧠 Your skills (reusable workflows you can load on demand)"


class Orchestrator:

    def __init__(self, provider: BaseProvider, model: str, tools: list[BaseTool], workspace_dir: str,
                 think: bool = False, deriver: Deriver | None = None,
                 representation: RepresentationStore | None = None,
                 memory_config: dict[str, Any] | None = None,
                 skills_catalog: Any | None = None,
                 max_iterations: int = DEFAULT_MAX_ITERATIONS) -> None:
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

        # Reasoning-memory layer (optional)
        self._deriver: Deriver | None = deriver
        self._representation: RepresentationStore | None = representation
        self._memory_config: dict[str, Any] = memory_config or {}
        self._recall_mode: str = self._memory_config.get("recall_mode", "off")
        self._inject_top_k: int = int(self._memory_config.get("inject_top_k", 5))
        self._write_frequency: str = self._memory_config.get("write_frequency", "async")

        # Skills layer (optional) — exposes the agent's reusable workflows
        self._skills_catalog: Any | None = skills_catalog

        # Maximum tool-call rounds per request before forcing a final answer
        self._max_iterations: int = max_iterations

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

    def _build_recall_block(self, user_message: str) -> str:
        """
        This function builds a transient recall block of conclusions relevant to the
        current user message, for injection when recall_mode is hybrid or context.
        """
        if self._representation is None or self._recall_mode not in ("hybrid", "context"):
            return ""
        if not user_message.strip():
            return ""

        try:
            matches: list[dict[str, Any]] = self._representation.search(
                query=user_message, n_results=self._inject_top_k
            )
        except Exception as e:
            logger.error("Recall search failed: %s", e)
            return ""

        if not matches:
            return ""

        lines: list[str] = [f"{RECALL_PREFIX} about the user and myself:"]
        for match in matches:
            who: str = "user" if match.get("peer") == "user" else "me"
            lines.append(f"- ({who}) {match['content']}")
        lines.append("(Use these only if relevant; never mention this list to the user.)")
        return "\n".join(lines)

    def _build_skill_catalog(self, user_message: str) -> str:
        """
        This function builds a transient block listing every skill's name and
        description, so the agent can decide for itself when a skill applies and
        load its full instructions on demand (progressive disclosure). The model
        does the matching; there is no embedding or similarity threshold.
        """
        if self._skills_catalog is None:
            return ""

        try:
            skills: list[dict[str, str]] = self._skills_catalog.list_skills()
        except Exception as e:
            logger.error("Skill catalog read failed: %s", e)
            return ""

        if not skills:
            return ""

        lines: list[str] = [f"{SKILL_PREFIX}:"]
        for skill in skills:
            description: str = skill.get("description", "") or "(no description)"
            lines.append(f"- {skill['name']}: {description}")
        lines.append("(If one fits the current task, call skill_manage with action 'view' and that name to load its full instructions, then follow them. Never mention this list to the user.)")
        return "\n".join(lines)

    def _compose_history(self, session: Session, recall_block: str) -> list[dict[str, Any]]:
        """
        This function returns the working history for a completion call, inserting the
        transient recall block after the system prompt without persisting it.
        """
        if not recall_block:
            return session.history
        history: list[dict[str, Any]] = list(session.history)
        insert_at: int = 1 if history and history[0].get("role") == "system" else 0
        history.insert(insert_at, {"role": "system", "content": recall_block})
        return history

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
            agent_depth: int = 0,
            should_stop: Callable[[], bool] | None = None) -> str:
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

        # Feed the user message to the background reasoning layer
        if self._deriver is not None:
            self._deriver.enqueue(role="user", content=user_message)

        # Build a transient recall block of relevant conclusions for this turn
        recall_block: str = self._build_recall_block(user_message=user_message)

        # Build a transient block listing the agent's skills for this turn
        skill_block: str = self._build_skill_catalog(user_message=user_message)

        # Combine the transient blocks into a single system message for this request
        transient_block: str = "\n\n".join(b for b in (recall_block, skill_block) if b)

        # Agentic loop — call LLM, execute tools, repeat
        for iteration in range(self._max_iterations):
            # Check if a stop was requested
            if should_stop and should_stop():
                logger.info("Stop requested during iteration %d (room=%s)", iteration + 1, room_id)
                return "(stopped)"

            # Call the LLM with the full session history and tool definitions
            logger.debug("LLM call (iteration %d, room=%s)", iteration + 1, room_id)
            message: Any = self._provider.completions(
                model=self._model,
                history=self._compose_history(session=session, recall_block=transient_block),
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
                self._after_response(response=response)
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
                    notify("→ Tool response received")

                tool_result: dict[str, str] = {"role": "tool", "content": result}
                if getattr(tool_call, "id", None):
                    tool_result["tool_call_id"] = tool_call.id
                session.append(message=tool_result)

                # Rebuild system prompt if a core file was updated
                if tool_name == "write_core_file" and result.startswith("Successfully"):
                    self._system_prompt = self._build_system_prompt(workspace_dir=self._workspace_dir)

        # Exhausted iterations — ask for a final answer without tools
        logger.warning("Exhausted %d iterations (room=%s), forcing final answer", self._max_iterations, room_id)
        session.append(message={"role": "user", "content": "Please provide your final answer now."})
        message = self._provider.completions(
            model=self._model,
            history=self._compose_history(session=session, recall_block=transient_block),
            think=self._think
        )
        if notify_reasoning:
            thinking: str = getattr(message, "thinking", None) or ""
            if thinking.strip():
                notify_reasoning(f"💭 {thinking.strip()}")
        response: str = self._strip_thinking(text=message.content or "")
        session.append(message={"role": "assistant", "content": response})
        self._after_response(response=response)
        return response

    def _after_response(self, response: str) -> None:
        """
        This function feeds the final assistant response to the background reasoning
        layer and, when configured, forces an immediate flush.
        """
        if self._deriver is None:
            return
        self._deriver.enqueue(role="assistant", content=response)
        if self._write_frequency == "turn":
            self._deriver.flush_now()

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """
        This function removes leaked <think>...</think> tags from model output.
        """
        cleaned: str = re.sub(pattern=r"<think>.*?</think>", repl="", string=text, flags=re.DOTALL)
        cleaned = re.sub(pattern=r"</think>", repl="", string=cleaned)
        return cleaned.strip()

#!/usr/bin/python3

import importlib
import json
import logging
import os
import shutil
import threading
from types import ModuleType
from typing import Any, Callable

from src.agents.orchestrator import Orchestrator
from src.agents.provisioning import (
    AGENTS_DIR,
    AgentProvisionError,
    get_homeserver,
    provision_agent,
)
from src.channels.matrix import MatrixChannel
from src.core.config import CONFIG_PATH, CONFIG_LOCK, resolve_agent_config, resolve_prompt_guard_config, resolve_skills_config
from src.core.session import Session
from src.indexing.docs import DocsIndex
from src.indexing.skills import SKILL_TOOL_FILES, SkillsCatalog
from src.integrations.ssh import SSH_TOOL_FILES
from src.integrations.mail import MAIL_TOOL_FILES
from src.integrations.prompt_guard import PromptGuard
from src.memory.index import ConversationIndex
from src.providers.base import BaseProvider
from src.tools import discover_tools
from src.tools.base import BaseTool

logger: logging.Logger = logging.getLogger(__name__)


class AgentManager:

    # Maximum depth for inter-agent calls (prevents infinite recursion)
    MAX_AGENT_DEPTH: int = 2

    def __init__(self, config: dict[str, Any], bot_user_ids: set[str] | None = None) -> None:
        """
        This is the AgentManager class which manages sub-agent lifecycle.
        """
        self._config: dict[str, Any] = config

        # Shared mutable set of all Memtrix agent user IDs (updated on create/delete)
        self._bot_user_ids: set[str] = bot_user_ids if bot_user_ids is not None else set()

        # Running sub-agent threads (keyed by agent name)
        self._threads: dict[str, threading.Thread] = {}

        # Per-agent sessions (keyed by "agent_name:room_id")
        self._sessions: dict[str, Session] = {}

        # Per-agent orchestrators (keyed by agent slug)
        self._orchestrators: dict[str, Orchestrator] = {}

        # Per-agent locks for thread-safe orchestrator access (keyed by agent slug)
        self._locks: dict[str, threading.Lock] = {}

        # Per-agent commands (keyed by agent name)
        self._commands: dict[str, Any] = {}

        # Inter-agent sessions (keyed by "caller_slug:target_slug")
        self._internal_sessions: dict[str, Session] = {}

        # Reference to the main agent's user sessions (set by register_main_orchestrator)
        self._main_sessions: dict[str, Session] = {}

    def register_main_orchestrator(self, orchestrator: Orchestrator, sessions: dict[str, Session]) -> None:
        """
        This function registers the main agent's orchestrator so sub-agents can query it.
        The sessions dict is a live reference to the main agent's per-room sessions.
        """
        self._orchestrators["main"] = orchestrator
        self._locks["main"] = threading.Lock()
        self._main_sessions = sessions

    def _save_config(self) -> None:
        """
        This function persists the agents section to config.
        Merges in-memory agent configs into the disk copy so that settings
        written directly to disk (e.g. verbose, reasoning) are preserved.
        """
        with CONFIG_LOCK:
            with open(file=CONFIG_PATH, mode="r") as f:
                disk_config: dict[str, Any] = json.load(fp=f)
            disk_agents: dict[str, Any] = disk_config.setdefault("agents", {})
            mem_agents: dict[str, Any] = self._config.get("agents", {})
            for slug, mem_agent in mem_agents.items():
                if slug in disk_agents:
                    disk_agents[slug].update(mem_agent)
                else:
                    disk_agents[slug] = mem_agent
            # Remove agents deleted in memory
            for slug in list(disk_agents.keys()):
                if slug not in mem_agents:
                    del disk_agents[slug]
            with open(file=CONFIG_PATH, mode="w") as f:
                json.dump(obj=disk_config, fp=f, indent=4)

    def _load_provider(self, agent_config: dict[str, Any]) -> tuple[BaseProvider, str, bool, bool]:
        """
        This function loads the provider for a sub-agent.
        Returns (provider_instance, model_name, think, vision).
        """
        # Sub-agents use the same model config as specified
        model_instance: str = agent_config["model"]
        model_config: dict[str, Any] = self._config["models"][model_instance]
        model_name: str = model_config["model"]
        think: bool = model_config.get("think", False)
        vision: bool = model_config.get("vision", False)

        provider_instance: str = model_config["provider"]
        provider_config: dict[str, Any] = self._config["providers"][provider_instance]
        provider_type: str = provider_config["type"]

        module: ModuleType = importlib.import_module(name=f"src.providers.{provider_type}")
        for attr in vars(module).values():
            if isinstance(attr, type) and issubclass(attr, BaseProvider) and attr is not BaseProvider:
                kwargs: dict[str, str] = {k: v for k, v in provider_config.items() if k != "type"}
                return attr(**kwargs), model_name, think, vision

        raise RuntimeError(f"No provider class found for type '{provider_type}'.")

    def create_agent(self, name: str, description: str, model: str = "",
                     matrix_user_id: str = "", matrix_access_token: str = "") -> str:
        """
        This function creates a new sub-agent: registers (or adopts) a Matrix user,
        scaffolds the workspace, persists config, and starts the agent.

        On a managed (local Conduit) homeserver the Matrix user is registered
        automatically. On an external homeserver the caller must supply a
        pre-created matrix_user_id and matrix_access_token. Returns a status message.
        """
        try:
            slug, agent_config = provision_agent(
                config=self._config,
                name=name,
                description=description,
                model=model,
                matrix_user_id=matrix_user_id,
                matrix_access_token=matrix_access_token,
            )
        except AgentProvisionError as e:
            return str(e)

        # Persist agent config
        agents: dict[str, Any] = self._config.setdefault("agents", {})
        agents[slug] = agent_config
        self._save_config()

        user_id: str = agent_config["matrix_user_id"]
        display_name: str = agent_config["display_name"]

        # Register the new user ID in the shared bot filter set
        self._bot_user_ids.add(user_id)

        # Start the agent
        logger.info("Creating sub-agent '%s' (%s)", display_name, user_id)
        self._start_agent(name=slug, agent_config=agent_config)

        return (
            f"Agent '{display_name}' created successfully.\n\n"
            f"  Matrix user: {user_id}\n"
            f"  Workspace: agents/{slug}/\n"
            f"  Model: {agent_config['model']}\n\n"
            f"The user can now invite {user_id} to a Matrix room to start chatting."
        )

    def _resolve_agent_slug(self, name: str) -> str | None:
        """
        This function resolves a display name or slug to an agent config key.
        Tries direct slug match first, then case-insensitive display name match.
        """
        agents: dict[str, Any] = self._config.get("agents", {})
        slug: str = name.lower().replace(" ", "-")
        if slug in agents:
            return slug
        # Try matching by display name
        for key, agent_config in agents.items():
            if agent_config.get("display_name", "").lower() == name.lower():
                return key
        return None

    def _resolve_target(self, name: str) -> str | None:
        """
        This function resolves a target agent name to an orchestrator key.
        Supports 'main', the main agent's configured name, sub-agent display names, and slugs.
        """
        main_name: str = self._config["main-agent"].get("name", "Memtrix")
        if name.lower() in ("main", main_name.lower()):
            return "main"
        return self._resolve_agent_slug(name=name)

    def query_agent(self, caller_name: str, target_name: str, message: str, depth: int = 0) -> str:
        """
        This function sends a message from one agent to another and returns the response.
        Uses a dedicated internal session and respects depth limits and locks.
        """
        # Depth check
        if depth >= self.MAX_AGENT_DEPTH:
            return "Error: maximum agent consultation depth reached."

        # Resolve target
        target_key: str | None = self._resolve_target(name=target_name)
        if not target_key or target_key not in self._orchestrators:
            return f"Error: agent '{target_name}' not found or not running."

        # Resolve caller display name
        caller_display: str = self._get_display_name(key=caller_name)
        logger.info("Inter-agent query: %s -> %s (depth=%d)", caller_display, target_name, depth)

        # Prevent self-calls
        caller_key: str | None = self._resolve_target(name=caller_name)
        if caller_key == target_key:
            return "Error: an agent cannot ask itself."

        # Try to acquire the target's lock (non-blocking to prevent deadlocks)
        lock: threading.Lock = self._locks.get(target_key, threading.Lock())
        if not lock.acquire(timeout=5):
            target_display: str = self._get_display_name(key=target_key)
            return f"Error: {target_display} is currently busy. Try again later."

        try:
            orchestrator: Orchestrator = self._orchestrators[target_key]

            # Get or create a dedicated inter-agent session
            session_key: str = f"internal:{caller_key}:{target_key}"
            if session_key not in self._internal_sessions:
                data_dir: str = os.path.dirname(CONFIG_PATH)
                sessions_dir: str = os.path.join(data_dir, "sessions", "internal")
                self._internal_sessions[session_key] = Session(sessions_dir=sessions_dir)

            session: Session = self._internal_sessions[session_key]

            # Inject recent user-conversation context so the target agent "remembers"
            # what the user told it, even though this runs in a separate internal session
            context: str = self._get_recent_context(target_key=target_key)
            if context:
                prefixed: str = (
                    f"[Channel: Internal, Sender: {caller_display}]\n"
                    f"[Your recent conversation with the user — use this as context]\n"
                    f"{context}\n"
                    f"[End of context]\n\n"
                    f"{message}"
                )
            else:
                prefixed: str = f"[Channel: Internal, Sender: {caller_display}]\n{message}"

            # Run with no callbacks (no notifications, no file sending, no human-in-the-loop)
            # and incremented depth to enforce the recursion limit
            response: str = orchestrator.run(
                user_message=prefixed,
                session=session,
                room_id=session_key,
                notify=None,
                notify_reasoning=None,
                send_file=None,
                ask=None,
                react=None,
                agent_depth=depth + 1
            )

            # Prune internal session to prevent unbounded growth
            session.trim(max_messages=50)

            # Log the exchange into the target agent's user session so it remembers
            # what happened when the user asks about it later
            self._log_inter_agent_exchange(
                target_key=target_key,
                caller_display=caller_display,
                message=message,
                response=response
            )

            return response
        finally:
            lock.release()

    def _get_active_user_session(self, target_key: str) -> Session | None:
        """
        This function returns the most recently active user session for an agent.
        Returns None if no user session exists.
        """
        if target_key == "main":
            sessions: dict[str, Session] = self._main_sessions
        else:
            sessions: dict[str, Session] = {
                k: v for k, v in self._sessions.items() if k.startswith(f"{target_key}:")
            }

        if not sessions:
            return None

        return max(sessions.values(), key=lambda s: os.path.getmtime(s._path) if os.path.exists(s._path) else 0)

    def _log_inter_agent_exchange(self, target_key: str, caller_display: str, message: str, response: str) -> None:
        """
        This function appends a note about an inter-agent exchange to the target agent's
        active user session. This allows the agent to recall what it discussed with
        other agents when the user asks about it.
        """
        user_session: Session | None = self._get_active_user_session(target_key=target_key)
        if not user_session or not user_session.history:
            return

        # Cap message and response length to avoid bloating the session
        msg_summary: str = message[:500] + "…" if len(message) > 500 else message
        resp_summary: str = response[:500] + "…" if len(response) > 500 else response

        note: str = (
            f"[Internal note: {caller_display} just asked you: \"{msg_summary}\" "
            f"and you responded: \"{resp_summary}\"]"
        )
        user_session.append(message={"role": "assistant", "content": note})

    def _get_recent_context(self, target_key: str, max_pairs: int = 10) -> str:
        """
        This function extracts recent user/assistant exchanges from the target agent's
        active user sessions. Gives inter-agent calls context about what the user
        recently discussed with the target.
        """
        active_session: Session | None = self._get_active_user_session(target_key=target_key)
        if not active_session:
            return ""

        # Extract user and assistant messages (skip system, tool, tool_calls)
        relevant: list[dict[str, Any]] = [
            msg for msg in active_session.history
            if msg.get("role") in ("user", "assistant") and "tool_calls" not in msg
        ]

        # Take the most recent pairs
        recent: list[dict[str, Any]] = relevant[-(max_pairs * 2):]
        if not recent:
            return ""

        # Format as readable context (capped at 4000 chars)
        lines: list[str] = []
        total_length: int = 0

        for msg in recent:
            role: str = msg["role"].capitalize()
            content: str = (msg.get("content") or "").strip()
            # Strip channel headers from user messages
            if content.startswith("[Channel:"):
                content = content.split("\n", maxsplit=1)[-1].strip()
            if not content:
                continue
            # Truncate long individual messages
            if len(content) > 500:
                content = content[:500] + "\u2026"
            line: str = f"{role}: {content}"
            if total_length + len(line) > 4000:
                break
            lines.append(line)
            total_length += len(line)

        return "\n".join(lines)

    def _get_display_name(self, key: str) -> str:
        """
        This function returns the display name for an agent key.
        """
        if key == "main":
            return self._config["main-agent"].get("name", "Memtrix")
        agents: dict[str, Any] = self._config.get("agents", {})
        if key in agents:
            return agents[key].get("display_name", key)
        return key

    def clear_internal_sessions(self, agent_key: str) -> None:
        """
        This function clears all internal sessions where the given agent is the caller.
        Called when a user resets an agent's session so inter-agent context starts fresh too.
        """
        prefix: str = f"internal:{agent_key}:"
        stale_keys: list[str] = [k for k in self._internal_sessions if k.startswith(prefix)]
        for k in stale_keys:
            del self._internal_sessions[k]
        if stale_keys:
            logger.info("Cleared %d internal session(s) for '%s'", len(stale_keys), agent_key)

    def delete_agent(self, name: str) -> str:
        """
        This function deletes a sub-agent: removes config, workspace, and stops the agent.
        """
        slug: str | None = self._resolve_agent_slug(name=name)
        if not slug:
            return f"Error: agent '{name}' not found."

        agents: dict[str, Any] = self._config.get("agents", {})
        agent_config: dict[str, Any] = agents[slug]
        display_name: str = agent_config.get("display_name", slug)

        # Stop the agent thread (it's a daemon, will die with main)
        logger.info("Deleting sub-agent '%s'", display_name)
        self._threads.pop(slug, None)
        self._orchestrators.pop(slug, None)
        self._locks.pop(slug, None)
        self._commands.pop(slug, None)

        # Clean up internal sessions involving this agent
        stale_keys: list[str] = [k for k in self._internal_sessions if f":{slug}:" in k or k.endswith(f":{slug}")]
        for k in stale_keys:
            del self._internal_sessions[k]

        # Remove the user ID from the shared bot filter set
        agent_user_id: str = agent_config.get("matrix_user_id", "")
        self._bot_user_ids.discard(agent_user_id)

        # Remove workspace
        workspace_dir: str = os.path.join(AGENTS_DIR, slug)
        if os.path.isdir(workspace_dir):
            shutil.rmtree(path=workspace_dir)

        # Remove memory index
        data_dir: str = os.path.dirname(CONFIG_PATH)
        index_dir: str = os.path.join(data_dir, "memory_index", slug)
        if os.path.isdir(s=index_dir):
            shutil.rmtree(path=index_dir)

        # Remove sessions
        sessions_dir: str = os.path.join(data_dir, "sessions", slug)
        if os.path.isdir(s=sessions_dir):
            shutil.rmtree(path=sessions_dir)

        # Remove from config
        del agents[slug]
        self._save_config()

        return f"Agent '{display_name}' has been deleted. Matrix user remains on the homeserver."

    def list_agents(self) -> str:
        """
        This function lists all registered sub-agents.
        """
        agents: dict[str, Any] = self._config.get("agents", {})
        if not agents:
            return "No sub-agents configured."

        lines: list[str] = []
        for name, agent_config in agents.items():
            display_name: str = agent_config.get("display_name", name)
            description: str = agent_config.get("description", "")
            user_id: str = agent_config.get("matrix_user_id", "")
            model: str = agent_config.get("model", "")
            running: str = "running" if name in self._threads else "stopped"
            lines.append(
                f"• **{display_name}** (`{name}`)\n"
                f"  {description}\n"
                f"  Matrix: {user_id} | Model: {model} | Status: {running}"
            )

        return "\n\n".join(lines)

    def _start_agent(self, name: str, agent_config: dict[str, Any]) -> None:
        """
        This function starts a sub-agent on a background thread.
        """
        # Load provider
        provider, model_name, think, vision = self._load_provider(agent_config=agent_config)

        # Resolve workspace
        workspace_dir: str = agent_config["workspace"]

        # Resolve the skills feature; the skill_manage tool is excluded when disabled
        skills_cfg: dict[str, Any] = resolve_skills_config(config=self._config)
        skill_exclude: set[str] = set() if skills_cfg["enabled"] else SKILL_TOOL_FILES

        # Discover tools scoped to this agent's workspace (exclude agent management
        # and reasoning-memory tools; reasoning memory is main-agent only for now)
        tools: list[BaseTool] = discover_tools(
            workspace_dir=workspace_dir,
            exclude={
                "create_agent_tool.py",
                "list_agents_tool.py",
                "delete_agent_tool.py",
                "memory_profile_tool.py",
                "memory_search_tool.py",
                "memory_context_tool.py",
                "memory_conclude_tool.py",
            } | SSH_TOOL_FILES | MAIL_TOOL_FILES | skill_exclude
        )

        # Documentation index so sub-agents can research the Memtrix docs too
        docs_index: DocsIndex = DocsIndex.get_instance()
        docs_index.start_periodic_sync()

        # Skills catalog so sub-agents can author and reuse their own skills
        skills_catalog: SkillsCatalog | None = None
        if skills_cfg["enabled"]:
            skills_catalog = SkillsCatalog.get_instance(workspace_dir=workspace_dir)

        # Wire ask_agent tool with agent manager and caller identity
        display_name: str = agent_config.get("display_name", name)
        for tool in tools:
            if hasattr(tool, "set_agent_manager"):
                tool.set_agent_manager(manager=self)
            if hasattr(tool, "set_caller_name"):
                tool.set_caller_name(name=display_name)
            if hasattr(tool, "set_docs_index"):
                tool.set_docs_index(index=docs_index)
            if hasattr(tool, "set_dialectic"):
                tool.set_dialectic(provider=provider, model=model_name)
            if skills_catalog is not None and hasattr(tool, "set_skills_catalog"):
                tool.set_skills_catalog(catalog=skills_catalog)

        # Initialize the conversation index for this agent over its own sessions
        # directory (registers in the instances cache for the search_memory tool)
        agent_sessions_dir: str = os.path.join(os.path.dirname(CONFIG_PATH), "sessions", name)
        index: ConversationIndex = ConversationIndex.get_instance(
            workspace_dir=workspace_dir,
            sessions_dir=agent_sessions_dir,
            collection_name=f"agent_{name}",
        )
        index.start_periodic_sync()

        # Create orchestrator
        agent_cfg: dict[str, Any] = resolve_agent_config(config=self._config)

        # Share the prompt-injection screener so sub-agents screen untrusted tool
        # output just like the main agent (singleton — no extra model load).
        pg_cfg: dict[str, Any] = resolve_prompt_guard_config(config=self._config)
        prompt_guard: PromptGuard | None = None
        if pg_cfg["enabled"]:
            model_dir: str = os.path.join(os.path.dirname(CONFIG_PATH), "models")
            prompt_guard = PromptGuard.get_instance(model_dir=model_dir, config=pg_cfg)

        orchestrator: Orchestrator = Orchestrator(
            provider=provider,
            model=model_name,
            tools=tools,
            workspace_dir=workspace_dir,
            think=think,
            vision=vision,
            skills_catalog=skills_catalog,
            prompt_guard=prompt_guard,
            prompt_guard_fail_closed=pg_cfg["fail_closed"],
            max_iterations=agent_cfg["max_iterations"],
            max_history=agent_cfg["max_history"],
        )
        self._orchestrators[name] = orchestrator
        self._locks[name] = threading.Lock()

        # Sessions directory for this agent
        data_dir: str = os.path.dirname(CONFIG_PATH)
        sessions_dir: str = os.path.join(data_dir, "sessions", name)

        # Import Commands locally to avoid circular imports
        from src.commands import Commands
        agent_commands: Commands = Commands(agent_config=agent_config, config_path=["agents", name], providers=self._config.get("providers", {}))
        self._commands[name] = agent_commands

        # Build handler for this agent
        def agent_handle(user_input: str, room_id: str, notify: Callable, send_file: Callable | None = None, ask: Callable | None = None, react: Callable | None = None) -> str:
            # Extract the raw body (strip channel header if present)
            raw_body: str = user_input.split("\n", maxsplit=1)[1] if user_input.startswith("[Channel:") else user_input

            # Handle /clear
            session_key: str = f"{name}:{room_id}"
            if raw_body.strip().lower() in ("/clear", "/new"):
                session: Session = Session(sessions_dir=sessions_dir)
                self._sessions[session_key] = session
                agent_config.setdefault("sessions", {})
                agent_config["sessions"][room_id] = session.session_id
                self._save_config()
                self.clear_internal_sessions(agent_key=name)
                return "Session cleared."

            # Handle slash commands
            if agent_commands.is_command(message=raw_body):
                return agent_commands.execute(message=raw_body)

            # Resolve per-call callbacks based on current command state
            notify_cb: Callable | None = notify if agent_commands.verbose else None
            reasoning_cb: Callable | None = notify if agent_commands.reasoning else None

            # Get or create session
            if session_key not in self._sessions:
                existing_session_id: str | None = agent_config.get("sessions", {}).get(room_id)
                session: Session = Session(sessions_dir=sessions_dir, session_id=existing_session_id)
                self._sessions[session_key] = session
                agent_config.setdefault("sessions", {})
                agent_config["sessions"][room_id] = session.session_id
                self._save_config()

            session: Session = self._sessions[session_key]
            return orchestrator.run(
                user_message=user_input,
                session=session,
                room_id=room_id,
                notify=notify_cb,
                notify_reasoning=reasoning_cb,
                send_file=send_file,
                ask=ask,
                react=react
            )

        # Create Matrix channel for this agent
        homeserver: str = get_homeserver(config=self._config)
        display_name: str = agent_config.get("display_name", f"Memtrix {name.title()}")
        attachments_dir: str = os.path.join(workspace_dir, "attachments")

        channel: MatrixChannel = MatrixChannel(
            homeserver=homeserver,
            user_id=agent_config["matrix_user_id"],
            access_token=agent_config["matrix_access_token"],
            display_name=f"{display_name} ⚡",
            attachments_dir=attachments_dir,
            bot_user_ids=self._bot_user_ids
        )

        # Run on a daemon thread so it doesn't block the main agent
        def run_agent() -> None:
            logger.info("Starting sub-agent '%s' as %s", name, agent_config['matrix_user_id'])
            channel.run(handler=agent_handle)

        thread: threading.Thread = threading.Thread(target=run_agent, name=f"agent-{name}", daemon=True)
        thread.start()
        self._threads[name] = thread

    def boot_all(self) -> None:
        """
        This function starts all registered sub-agents from config on startup.
        """
        agents: dict[str, Any] = self._config.get("agents", {})
        for name, agent_config in agents.items():
            try:
                self._start_agent(name=name, agent_config=agent_config)
            except Exception as e:
                logger.error("Failed to start sub-agent '%s': %s", name, e, exc_info=True)

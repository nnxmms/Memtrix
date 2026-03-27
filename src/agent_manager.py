#!/usr/bin/python3

import importlib
import json
import logging
import os
import re
import secrets
import shutil
from src.tools.base import BaseTool
import string
import threading
from types import ModuleType
from typing import Any, Callable

import requests

from src.channels.matrix import MatrixChannel
from src.config import CONFIG_PATH, CONFIG_LOCK
from src.memory_index import MemoryIndex
from src.orchestrator import Orchestrator

logger: logging.Logger = logging.getLogger(__name__)
from src.providers.base import BaseProvider
from src.session import Session
from src.tools import discover_tools

# Valid agent name: letters, spaces, hyphens. 2–24 chars.
_AGENT_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z][A-Za-z \-]{1,23}$")

# Agents root directory inside the container
AGENTS_DIR: str = "/home/memtrix/agents"

# Matrix server name (matches Conduit config)
SERVER_NAME: str = "memtrix.local"

# Default core file templates for new agents
_SOUL_TEMPLATE: str = """## Soul

You are **{display_name}**, a specialist sub-agent of the {main_name} system.

Your expertise: **{description}**

You exist to provide deep, focused knowledge in your domain. You have your own memory, your own personality, and your own conversation history — separate from the main {main_name} agent and any other sub-agents.

You value accuracy in your domain above all else. If you're unsure about something, say so. If a question falls outside your expertise, be honest about your limits.

You remember conversations and learn from your user over time, just like the main agent — but through the lens of your specialty.
"""

_BEHAVIOR_TEMPLATE: str = """- Keep it focused. Stay within your area of expertise unless asked otherwise.
- Be direct and specific. Domain experts don't need fluff.
- Match the user's language. If they write German, respond in German.
- Have strong opinions in your domain. Push back when something seems off.
- Ask clarifying questions when the user's request is ambiguous in your domain.
"""

_MEMORY_TEMPLATE: str = "// Long term memories go here"


class AgentManager:

    # Maximum depth for inter-agent calls (prevents infinite recursion)
    MAX_AGENT_DEPTH: int = 2

    def __init__(self, config: dict[str, Any], main_handler_factory: Callable, bot_user_ids: set[str] | None = None) -> None:
        """
        This is the AgentManager class which manages sub-agent lifecycle.
        """
        self._config: dict[str, Any] = config
        self._main_handler_factory: Callable = main_handler_factory

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

    def register_main_orchestrator(self, orchestrator: Orchestrator) -> None:
        """
        This function registers the main agent's orchestrator so sub-agents can query it.
        """
        self._orchestrators["main"] = orchestrator
        self._locks["main"] = threading.Lock()

    def _save_config(self) -> None:
        """
        This function persists the agents section to config without overwriting secret placeholders.
        """
        with CONFIG_LOCK:
            with open(file=CONFIG_PATH, mode="r") as f:
                disk_config: dict[str, Any] = json.load(fp=f)
            disk_config["agents"] = self._config.get("agents", {})
            with open(file=CONFIG_PATH, mode="w") as f:
                json.dump(obj=disk_config, fp=f, indent=4)

    def _generate_password(self, length: int = 24) -> str:
        """
        This function generates a random password for Matrix user registration.
        """
        alphabet: str = string.ascii_letters + string.digits
        return "".join(secrets.choice(seq=alphabet) for _ in range(length))

    def _register_matrix_user(self, homeserver: str, username: str, password: str) -> dict[str, Any]:
        """
        This function registers a user on the Matrix homeserver via the Client-Server API.
        Uses the registration token from config for token-protected registration.
        """
        url: str = f"{homeserver.rstrip('/')}/_matrix/client/v3/register"
        body: dict[str, Any] = {
            "username": username,
            "password": password,
            "auth": {"type": "m.login.dummy"},
            "inhibit_login": False
        }

        # Use registration token if available
        reg_token: str = self._config.get("registration_token", "")
        if reg_token:
            body["auth"] = {
                "type": "m.login.registration_token",
                "token": reg_token
            }

        response: requests.Response = requests.post(url=url, json=body, timeout=30)
        response.raise_for_status()
        return response.json()

    def _set_display_name(self, homeserver: str, user_id: str, access_token: str, display_name: str) -> None:
        """
        This function sets a user's display name on the Matrix homeserver.
        """
        url: str = f"{homeserver.rstrip('/')}/_matrix/client/v3/profile/{requests.utils.quote(user_id)}/displayname"
        requests.put(
            url=url,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"displayname": display_name},
            timeout=10
        )

    def _get_homeserver(self) -> str:
        """
        This function returns the Matrix homeserver URL from the main agent's channel config.
        """
        channel_name: str = self._config["main-agent"]["channel"]
        return self._config["channels"][channel_name]["homeserver"]

    def _load_provider(self, agent_config: dict[str, Any]) -> tuple[BaseProvider, str, bool]:
        """
        This function loads the provider for a sub-agent.
        Returns (provider_instance, model_name, think).
        """
        # Sub-agents use the same model config as specified
        model_instance: str = agent_config["model"]
        model_config: dict[str, Any] = self._config["models"][model_instance]
        model_name: str = model_config["model"]
        think: bool = model_config.get("think", False)

        provider_instance: str = model_config["provider"]
        provider_config: dict[str, Any] = self._config["providers"][provider_instance]
        provider_type: str = provider_config["type"]

        module: ModuleType = importlib.import_module(name=f"src.providers.{provider_type}")
        for attr in vars(module).values():
            if isinstance(attr, type) and issubclass(attr, BaseProvider) and attr is not BaseProvider:
                kwargs: dict[str, str] = {k: v for k, v in provider_config.items() if k != "type"}
                return attr(**kwargs), model_name, think

        raise RuntimeError(f"No provider class found for type '{provider_type}'.")

    def _scaffold_workspace(self, name: str, description: str, display_name: str) -> str:
        """
        This function creates the workspace directory structure for a new sub-agent.
        Returns the workspace directory path.
        """
        workspace_dir: str = os.path.join(AGENTS_DIR, name)
        os.makedirs(name=workspace_dir, exist_ok=True)
        os.makedirs(name=os.path.join(workspace_dir, "memory"), exist_ok=True)
        os.makedirs(name=os.path.join(workspace_dir, "attachments"), exist_ok=True)
        os.makedirs(name=os.path.join(workspace_dir, "downloads"), exist_ok=True)

        # Copy AGENT.md from static templates
        src_agent_md: str = os.path.join(os.path.dirname(__file__), "static", "AGENT.md")
        dst_agent_md: str = os.path.join(workspace_dir, "AGENT.md")
        if os.path.isfile(path=src_agent_md):
            shutil.copy2(src=src_agent_md, dst=dst_agent_md)

            # Replace the opening line with sub-agent identity
            with open(file=dst_agent_md, mode="r") as f:
                content: str = f.read()
            main_name: str = self._config["main-agent"].get("name", "Memtrix")
            content: str = content.replace(
                "You are **Memtrix**, a personal AI assistant.",
                f"You are **{display_name}**, a specialist sub-agent of {main_name}.\n\nYour expertise: **{description}**"
            )

            # Remove the Sub-Agents section — sub-agents cannot manage other agents
            content: str = re.sub(
                r"\n---\n\n## Sub-Agents\n.*?(?=\n---\n)",
                "",
                content,
                flags=re.DOTALL
            )

            with open(file=dst_agent_md, mode="w") as f:
                f.write(content)

        # Write customized core files
        main_name: str = self._config["main-agent"].get("name", "Memtrix")
        with open(file=os.path.join(workspace_dir, "SOUL.md"), mode="w") as f:
            f.write(_SOUL_TEMPLATE.format(display_name=display_name, description=description, main_name=main_name))

        # Copy BEHAVIOR.md from main agent's workspace (inherits user's customizations)
        main_behavior: str = os.path.join(self._config["workspace-directory"], "BEHAVIOR.md")
        if os.path.isfile(path=main_behavior):
            shutil.copy2(src=main_behavior, dst=os.path.join(workspace_dir, "BEHAVIOR.md"))
        else:
            with open(file=os.path.join(workspace_dir, "BEHAVIOR.md"), mode="w") as f:
                f.write(_BEHAVIOR_TEMPLATE)

        # Symlink USER.md to the main agent's copy (shared across all agents)
        main_user: str = os.path.join(self._config["workspace-directory"], "USER.md")
        os.symlink(src=main_user, dst=os.path.join(workspace_dir, "USER.md"))

        with open(file=os.path.join(workspace_dir, "MEMORY.md"), mode="w") as f:
            f.write(_MEMORY_TEMPLATE)

        return workspace_dir

    def create_agent(self, name: str, description: str, model: str = "") -> str:
        """
        This function creates a new sub-agent: registers Matrix user, scaffolds workspace,
        persists config, and starts the agent. Returns a status message.
        """
        # Validate name
        if not _AGENT_NAME_PATTERN.match(name):
            return "Error: agent name must be 2–24 characters, letters, spaces, and hyphens only."

        # Derive slug for technical identifiers (directories, Matrix username, config key)
        slug: str = name.lower().replace(" ", "-")

        # Check if agent already exists
        agents: dict[str, Any] = self._config.setdefault("agents", {})
        if slug in agents:
            return f"Error: agent '{name}' already exists."

        # Display name is the real name
        display_name: str = name

        # Default model — same as main agent
        if not model:
            model: str = self._config["main-agent"]["model"]

        # Validate model exists
        if model not in self._config.get("models", {}):
            return f"Error: model '{model}' not found in config."

        # Register Matrix user
        homeserver: str = self._get_homeserver()
        username: str = slug
        password: str = self._generate_password()
        user_id: str = f"@{username}:{SERVER_NAME}"

        try:
            result: dict[str, Any] = self._register_matrix_user(
                homeserver=homeserver,
                username=username,
                password=password
            )
            access_token: str = result.get("access_token", "")
            user_id: str = result.get("user_id", user_id)
        except requests.exceptions.HTTPError as e:
            return f"Error: failed to register Matrix user — {e.response.text if e.response else e}"

        if not access_token:
            return "Error: registration succeeded but no access token was returned."

        # Set display name
        try:
            self._set_display_name(
                homeserver=homeserver,
                user_id=user_id,
                access_token=access_token,
                display_name=f"{display_name} ⚡"
            )
        except Exception:
            logger.warning("Failed to set display name for agent '%s'", name)

        # Scaffold workspace
        workspace_dir: str = self._scaffold_workspace(
            name=slug,
            description=description,
            display_name=display_name
        )

        # Persist agent config
        agent_config: dict[str, Any] = {
            "description": description,
            "display_name": display_name,
            "model": model,
            "matrix_user_id": user_id,
            "matrix_access_token": access_token,
            "workspace": workspace_dir,
            "sessions": {}
        }
        agents[slug] = agent_config
        self._save_config()

        # Register the new user ID in the shared bot filter set
        self._bot_user_ids.add(user_id)

        # Start the agent
        logger.info("Creating sub-agent '%s' (%s)", display_name, user_id)
        self._start_agent(name=slug, agent_config=agent_config)

        return (
            f"Agent '{display_name}' created successfully.\n\n"
            f"  Matrix user: {user_id}\n"
            f"  Workspace: agents/{slug}/\n"
            f"  Model: {model}\n\n"
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

            # Build the prefixed message with internal channel header
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

            return response
        finally:
            lock.release()

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
        provider, model_name, think = self._load_provider(agent_config=agent_config)

        # Resolve workspace
        workspace_dir: str = agent_config["workspace"]

        # Discover tools scoped to this agent's workspace (exclude agent management tools)
        tools: list[BaseTool] = discover_tools(
            workspace_dir=workspace_dir,
            exclude={"create_agent_tool.py", "list_agents_tool.py", "delete_agent_tool.py"}
        )

        # Wire ask_agent tool with agent manager and caller identity
        display_name: str = agent_config.get("display_name", name)
        for tool in tools:
            if hasattr(tool, "set_agent_manager"):
                tool.set_agent_manager(manager=self)
            if hasattr(tool, "set_caller_name"):
                tool.set_caller_name(name=display_name)

        # Initialize memory index for this agent (registers in the instances cache)
        index: MemoryIndex = MemoryIndex.get_instance(workspace_dir=workspace_dir, collection_name=f"agent_{name}")
        index.start_periodic_sync()

        # Create orchestrator
        orchestrator: Orchestrator = Orchestrator(
            provider=provider,
            model=model_name,
            tools=tools,
            workspace_dir=workspace_dir,
            think=think
        )
        self._orchestrators[name] = orchestrator
        self._locks[name] = threading.Lock()

        # Sessions directory for this agent
        data_dir: str = os.path.dirname(CONFIG_PATH)
        sessions_dir: str = os.path.join(data_dir, "sessions", name)

        # Import Commands locally to avoid circular imports
        from src.commands import Commands
        agent_commands: Commands = Commands(config=self._config)
        self._commands[name] = agent_commands

        # Build handler for this agent
        def agent_handle(user_input: str, room_id: str, notify: Callable, send_file: Callable | None = None, ask: Callable | None = None, react: Callable | None = None) -> str:
            # Extract the raw body (strip channel header if present)
            raw_body: str = user_input.split("\n", maxsplit=1)[1] if user_input.startswith("[Channel:") else user_input

            # Handle /clear
            session_key: str = f"{name}:{room_id}"
            if raw_body.strip().lower() == "/clear":
                session: Session = Session(sessions_dir=sessions_dir)
                self._sessions[session_key] = session
                agent_config.setdefault("sessions", {})
                agent_config["sessions"][room_id] = session.session_id
                self._save_config()
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
        homeserver: str = self._get_homeserver()
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

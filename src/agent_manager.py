#!/usr/bin/python3

import importlib
import json
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
from src.config import CONFIG_PATH
from src.memory_index import MemoryIndex
from src.orchestrator import Orchestrator
from src.providers.base import BaseProvider
from src.session import Session
from src.tools import discover_tools

# Valid agent name: lowercase letters, digits, hyphens. 2–24 chars.
_AGENT_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9\-]{1,23}$")

# Agents root directory inside the container
AGENTS_DIR: str = "/home/memtrix/agents"

# Matrix server name (matches Conduit config)
SERVER_NAME: str = "memtrix.local"

# Default core file templates for new agents
_SOUL_TEMPLATE: str = """## Soul

You are **{display_name}**, a specialist sub-agent of the Memtrix system.

Your expertise: **{description}**

You exist to provide deep, focused knowledge in your domain. You have your own memory, your own personality, and your own conversation history — separate from the main Memtrix agent and any other sub-agents.

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

    def __init__(self, config: dict[str, Any], main_handler_factory: Callable) -> None:
        """
        This is the AgentManager class which manages sub-agent lifecycle.
        """
        self._config: dict[str, Any] = config
        self._main_handler_factory: Callable = main_handler_factory

        # Running sub-agent threads (keyed by agent name)
        self._threads: dict[str, threading.Thread] = {}

        # Per-agent sessions (keyed by "agent_name:room_id")
        self._sessions: dict[str, Session] = {}

        # Per-agent orchestrators (keyed by agent name)
        self._orchestrators: dict[str, Orchestrator] = {}

        # Per-agent commands (keyed by agent name)
        self._commands: dict[str, Any] = {}

    def _save_config(self) -> None:
        """
        This function persists the agents section to config without overwriting secret placeholders.
        """
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

            # Replace "Memtrix" with the agent's display name in AGENT.md
            with open(file=dst_agent_md, mode="r") as f:
                content: str = f.read()
            content: str = content.replace(
                "You are **Memtrix**, a personal AI assistant.",
                f"You are **{display_name}**, a specialist sub-agent of Memtrix.\n\nYour expertise: **{description}**"
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
        with open(file=os.path.join(workspace_dir, "SOUL.md"), mode="w") as f:
            f.write(_SOUL_TEMPLATE.format(display_name=display_name, description=description))

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

    def create_agent(self, name: str, description: str, display_name: str = "", model: str = "") -> str:
        """
        This function creates a new sub-agent: registers Matrix user, scaffolds workspace,
        persists config, and starts the agent. Returns a status message.
        """
        # Validate name
        if not _AGENT_NAME_PATTERN.match(name):
            return "Error: agent name must be 2–24 characters, lowercase letters, digits, and hyphens only."

        # Check if agent already exists
        agents: dict[str, Any] = self._config.setdefault("agents", {})
        if name in agents:
            return f"Error: agent '{name}' already exists."

        # Default display name
        if not display_name:
            display_name: str = f"Memtrix {name.replace('-', ' ').title()}"

        # Default model — same as main agent
        if not model:
            model: str = self._config["main-agent"]["model"]

        # Validate model exists
        if model not in self._config.get("models", {}):
            return f"Error: model '{model}' not found in config."

        # Register Matrix user
        homeserver: str = self._get_homeserver()
        username: str = f"memtrix-{name}"
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
            pass  # Non-critical

        # Scaffold workspace
        workspace_dir: str = self._scaffold_workspace(
            name=name,
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
        agents[name] = agent_config
        self._save_config()

        # Start the agent
        self._start_agent(name=name, agent_config=agent_config)

        return (
            f"Agent '{display_name}' created successfully.\n\n"
            f"  Matrix user: {user_id}\n"
            f"  Workspace: agents/{name}/\n"
            f"  Model: {model}\n\n"
            f"The user can now invite {user_id} to a Matrix room to start chatting."
        )

    def delete_agent(self, name: str) -> str:
        """
        This function deletes a sub-agent: removes config, workspace, and stops the agent.
        """
        agents: dict[str, Any] = self._config.get("agents", {})
        if name not in agents:
            return f"Error: agent '{name}' not found."

        agent_config: dict[str, Any] = agents[name]
        display_name: str = agent_config.get("display_name", name)

        # Stop the agent thread (it's a daemon, will die with main)
        self._threads.pop(name, None)
        self._orchestrators.pop(name, None)

        # Remove workspace
        workspace_dir: str = os.path.join(AGENTS_DIR, name)
        if os.path.isdir(workspace_dir):
            shutil.rmtree(path=workspace_dir)

        # Remove memory index
        data_dir: str = os.path.dirname(CONFIG_PATH)
        index_dir: str = os.path.join(data_dir, "memory_index", name)
        if os.path.isdir(s=index_dir):
            shutil.rmtree(path=index_dir)

        # Remove sessions
        sessions_dir: str = os.path.join(data_dir, "sessions", name)
        if os.path.isdir(s=sessions_dir):
            shutil.rmtree(path=sessions_dir)

        # Remove from config
        del agents[name]
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

        # Sessions directory for this agent
        data_dir: str = os.path.dirname(CONFIG_PATH)
        sessions_dir: str = os.path.join(data_dir, "sessions", name)

        # Import Commands locally to avoid circular imports
        from src.commands import Commands
        agent_commands: Commands = Commands(config=self._config)
        self._commands[name] = agent_commands

        # Build handler for this agent
        def agent_handle(user_input: str, room_id: str, notify: Callable, send_file: Callable | None = None, ask: Callable | None = None) -> str:
            # Handle /clear
            session_key: str = f"{name}:{room_id}"
            if user_input.strip().lower() == "/clear":
                session: Session = Session(sessions_dir=sessions_dir)
                self._sessions[session_key] = session
                agent_config.setdefault("sessions", {})
                agent_config["sessions"][room_id] = session.session_id
                self._save_config()
                return "Session cleared."

            # Handle slash commands
            if agent_commands.is_command(message=user_input):
                return agent_commands.execute(message=user_input)

            # Set up callbacks
            if agent_commands.verbose:
                orchestrator.set_notify(callback=notify)
            else:
                orchestrator.set_notify(callback=None)

            if agent_commands.reasoning:
                orchestrator.set_notify_reasoning(callback=notify)
            else:
                orchestrator.set_notify_reasoning(callback=None)

            orchestrator.set_send_file(callback=send_file)
            orchestrator.set_ask(callback=ask)

            # Get or create session
            if session_key not in self._sessions:
                existing_session_id: str | None = agent_config.get("sessions", {}).get(room_id)
                session: Session = Session(sessions_dir=sessions_dir, session_id=existing_session_id)
                self._sessions[session_key] = session
                agent_config.setdefault("sessions", {})
                agent_config["sessions"][room_id] = session.session_id
                self._save_config()

            session: Session = self._sessions[session_key]
            return orchestrator.run(user_message=user_input, session=session, room_id=room_id)

        # Create Matrix channel for this agent
        homeserver: str = self._get_homeserver()
        display_name: str = agent_config.get("display_name", f"Memtrix {name.title()}")
        attachments_dir: str = os.path.join(workspace_dir, "attachments")

        channel: MatrixChannel = MatrixChannel(
            homeserver=homeserver,
            user_id=agent_config["matrix_user_id"],
            access_token=agent_config["matrix_access_token"],
            display_name=f"{display_name} ⚡",
            attachments_dir=attachments_dir
        )

        # Run on a daemon thread so it doesn't block the main agent
        def run_agent() -> None:
            print(f"Starting sub-agent '{name}' as {agent_config['matrix_user_id']}...")
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
                print(f"Error starting sub-agent '{name}': {e}")

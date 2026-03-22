#!/usr/bin/python3

import importlib
import json
import os
from types import ModuleType
from typing import Any, Callable

from src.channels.cli import CLIChannel
from src.channels.matrix import MatrixChannel
from src.commands import Commands
from src.config import CONFIG_PATH
from src.memory_index import MemoryIndex
from src.orchestrator import Orchestrator
from src.providers.base import BaseProvider
from src.session import Session
from src.tools import discover_tools


class Memtrix:

    def __init__(self, config: dict[str, Any]) -> None:
        """
        This is the Memtrix class which wires together the channel and provider.
        """
        # Full configuration
        self._config: dict[str, Any] = config

        # Active provider instance
        self._provider: BaseProvider | None = None

        # Active model name on the provider
        self._model: str = ""

        # Orchestrator for the agentic loop
        self._orchestrator: Orchestrator | None = None

        # Slash commands
        self._commands: Commands = Commands(config=config)

        # Per-room sessions keyed by room id
        self._sessions: dict[str, Session] = {}

        # Sessions directory
        self._sessions_dir: str = os.path.join(os.path.dirname(CONFIG_PATH), "sessions")

    def _load_provider(self) -> None:
        """
        This function loads the provider configured for the main agent.
        """
        # Resolve model → provider chain from config
        model_instance: str = self._config["main-agent"]["model"]
        model_config: dict[str, Any] = self._config["models"][model_instance]
        self._model = model_config["model"]

        provider_instance: str = model_config["provider"]
        provider_config: dict[str, Any] = self._config["providers"][provider_instance]

        # Dynamically import the provider module
        provider_type: str = provider_config["type"]
        module: ModuleType = importlib.import_module(name=f"src.providers.{provider_type}")

        # Find the BaseProvider subclass and instantiate it
        for attr in vars(module).values():
            if isinstance(attr, type) and issubclass(attr, BaseProvider) and attr is not BaseProvider:
                kwargs: dict[str, str] = {k: v for k, v in provider_config.items() if k != "type"}
                self._provider = attr(**kwargs)
                break
        else:
            raise RuntimeError(f"No provider class found for type '{provider_type}'.")

        # Discover tools and create the orchestrator
        workspace_dir: str = self._config["workspace-directory"]
        tools: list[BaseTool] = discover_tools(workspace_dir=workspace_dir)
        think: bool = model_config.get("think", False)

        # Eagerly initialize the memory index so existing files are indexed at startup
        index: MemoryIndex = MemoryIndex.get_instance(workspace_dir=workspace_dir)
        index.start_periodic_sync()

        self._orchestrator = Orchestrator(
            provider=self._provider,
            model=self._model,
            tools=tools,
            workspace_dir=workspace_dir,
            think=think
        )

        # Load existing sessions from config
        sessions_map: dict[str, str] = self._config.get("main-agent", {}).get("sessions", {})
        for room_id, session_id in sessions_map.items():
            self._sessions[room_id] = Session(sessions_dir=self._sessions_dir, session_id=session_id)

    def _save_sessions(self) -> None:
        """
        This function persists the sessions mapping to config without overwriting secret placeholders.
        """
        with open(file=CONFIG_PATH, mode="r") as f:
            disk_config: dict[str, Any] = json.load(fp=f)
        disk_config["main-agent"]["sessions"] = self._config["main-agent"]["sessions"]
        with open(file=CONFIG_PATH, mode="w") as f:
            json.dump(obj=disk_config, fp=f, indent=4)

    def _get_session(self, room_id: str) -> Session:
        """
        This function returns the session for a room, creating one if it doesn't exist.
        """
        if room_id not in self._sessions:
            session: Session = Session(sessions_dir=self._sessions_dir)
            self._sessions[room_id] = session

            # Persist the new mapping to config (read-modify-write to preserve placeholders)
            self._config["main-agent"].setdefault("sessions", {})
            self._config["main-agent"]["sessions"][room_id] = session.session_id
            self._save_sessions()

        return self._sessions[room_id]

    def _clear_session(self, room_id: str) -> str:
        """
        This function creates a new session for the given room, replacing the old one.
        """
        session: Session = Session(sessions_dir=self._sessions_dir)
        self._sessions[room_id] = session

        # Persist the updated mapping to config (read-modify-write to preserve placeholders)
        self._config["main-agent"].setdefault("sessions", {})
        self._config["main-agent"]["sessions"][room_id] = session.session_id
        self._save_sessions()

        return "Session cleared."

    def _handle(self, user_input: str, room_id: str, notify: Callable[[str], None], send_file: Callable[[str], None] | None = None) -> str:
        """
        This function handles a user message and returns the response.
        The notify callback sends real-time status messages to the channel.
        The send_file callback sends a file to the channel.
        """
        # Handle /clear — needs access to sessions and room_id
        if user_input.strip().lower() == "/clear":
            return self._clear_session(room_id=room_id)

        # Check for slash commands
        if self._commands.is_command(message=user_input):
            return self._commands.execute(message=user_input)

        # Set up notify callback if verbose mode is on
        if self._commands.verbose:
            self._orchestrator.set_notify(callback=notify)
        else:
            self._orchestrator.set_notify(callback=None)

        # Set up reasoning callback if reasoning display is on
        if self._commands.reasoning:
            self._orchestrator.set_notify_reasoning(callback=notify)
        else:
            self._orchestrator.set_notify_reasoning(callback=None)

        # Set up send_file callback
        self._orchestrator.set_send_file(callback=send_file)

        # Get the session for this room and run the orchestrator
        session: Session = self._get_session(room_id=room_id)
        return self._orchestrator.run(user_message=user_input, session=session, room_id=room_id)

    def run(self) -> None:
        """
        This function starts Memtrix on the configured channel.
        """
        # Load the configured provider and orchestrator
        self._load_provider()

        # Resolve channel from config
        channel_instance: str = self._config["main-agent"]["channel"]
        channel_config: dict[str, Any] = self._config["channels"][channel_instance]
        channel_type: str = channel_config["type"]

        # Start the appropriate channel
        workspace_dir: str = self._config["workspace-directory"]
        attachments_dir: str = os.path.join(workspace_dir, "attachments")

        if channel_type == "matrix":
            channel: MatrixChannel = MatrixChannel(
                homeserver=channel_config["homeserver"],
                user_id=channel_config["user_id"],
                access_token=channel_config["access_token"],
                display_name=channel_config.get("display_name", "Memtrix ⚡"),
                attachments_dir=attachments_dir
            )
            channel.run(handler=self._handle)
        else:
            cli: CLIChannel = CLIChannel()
            cli.run(handler=self._handle)

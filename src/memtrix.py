#!/usr/bin/python3

import importlib
import logging
import os
from types import ModuleType
from typing import Any, Callable

from src.agent_manager import AgentManager
from src.channels.cli import CLIChannel
from src.channels.matrix import MatrixChannel
from src.commands import Commands
from src.config import CONFIG_PATH, resolve_skills_config, resolve_ssh_config, update_config
from src.deriver import Deriver
from src.docs_index import DocsIndex
from src.lifecycle import install_signal_handlers, start_heartbeat
from src.memory_index import MemoryIndex
from src.orchestrator import Orchestrator
from src.providers.base import BaseProvider
from src.representation import RepresentationStore, resolve_memory_config
from src.session import Session
from src.skills_index import SKILL_TOOL_FILES, SkillsIndex
from src.ssh_manager import SSH_TOOL_FILES, SSHManager
from src.tools import discover_tools

logger: logging.Logger = logging.getLogger(__name__)

# Reasoning-memory tool files, gated behind recall_mode
MEMORY_TOOL_FILES: set[str] = {
    "memory_profile_tool.py",
    "memory_search_tool.py",
    "memory_context_tool.py",
    "memory_conclude_tool.py",
}


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

        # Background reasoning deriver (when reasoning memory is enabled)
        self._deriver: Deriver | None = None

        # Slash commands
        self._commands: Commands = Commands(agent_config=config["main-agent"], config_path=["main-agent"], providers=config.get("providers", {}))

        # Agent manager for sub-agents
        self._agent_manager: AgentManager | None = None

        # Per-room sessions keyed by room id
        self._sessions: dict[str, Session] = {}

        # Shared mutable set of all bot user IDs (main + sub-agents)
        self._bot_user_ids: set[str] = set()

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
                logger.info("Loaded provider '%s' (%s)", provider_instance, provider_type)
                break
        else:
            raise RuntimeError(f"No provider class found for type '{provider_type}'.")

        # Discover tools and create the orchestrator
        workspace_dir: str = self._config["workspace-directory"]
        think: bool = model_config.get("think", False)

        # Resolve reasoning-memory configuration
        mem_cfg: dict[str, Any] = resolve_memory_config(config=self._config)

        # Reasoning model: default to the main model; allow an override on the same provider
        reasoning_model: str = self._model
        rm_name: Any = mem_cfg.get("reasoning_model")
        if rm_name and rm_name in self._config.get("models", {}):
            rm_cfg: dict[str, Any] = self._config["models"][rm_name]
            if rm_cfg.get("provider") == provider_instance:
                reasoning_model = rm_cfg.get("model", self._model)
            else:
                logger.warning(
                    "memory.reasoning_model '%s' uses a different provider; falling back to the main model",
                    rm_name,
                )

        # Build the representation store and background deriver when enabled
        representation: RepresentationStore | None = None
        deriver: Deriver | None = None
        if mem_cfg["enabled"]:
            representation = RepresentationStore.get_instance(workspace_dir=workspace_dir)
            deriver = Deriver(provider=self._provider, model=reasoning_model, store=representation, config=mem_cfg)
            deriver.start()
            deriver.start_consolidation_scheduler()
            self._deriver = deriver
            self._commands.register(name="consolidate", handler=self._cmd_consolidate)
            logger.info("Reasoning memory enabled (recall_mode=%s)", mem_cfg["recall_mode"])

        # Discover tools, excluding the reasoning-memory tools unless tool access is enabled
        tool_exclude: set[str] = set()
        if not (mem_cfg["enabled"] and mem_cfg["recall_mode"] in ("tools", "hybrid")):
            tool_exclude |= MEMORY_TOOL_FILES

        # Exclude the SSH sysadmin tools unless SSH administration is enabled
        ssh_cfg: dict[str, Any] = resolve_ssh_config(config=self._config)
        if not ssh_cfg["enabled"]:
            tool_exclude |= SSH_TOOL_FILES
        else:
            logger.info("SSH remote administration enabled")

        # Exclude the skill management tool unless the skills feature is enabled
        skills_cfg: dict[str, Any] = resolve_skills_config(config=self._config)
        if not skills_cfg["enabled"]:
            tool_exclude |= SKILL_TOOL_FILES

        tools: list[BaseTool] = discover_tools(workspace_dir=workspace_dir, exclude=tool_exclude)

        # Eagerly initialize the memory index so existing files are indexed at startup
        index: MemoryIndex = MemoryIndex.get_instance(workspace_dir=workspace_dir)
        index.start_periodic_sync()

        # Initialize the documentation index so the agent can research its own docs
        docs_index: DocsIndex = DocsIndex.get_instance()
        docs_index.start_periodic_sync()

        # Initialize the skills index so the agent can author and reuse skills
        skills_index: SkillsIndex | None = None
        if skills_cfg["enabled"]:
            skills_index = SkillsIndex.get_instance(workspace_dir=workspace_dir)
            skills_index.start_periodic_sync()
            logger.info("Skills enabled")

        # Wire reasoning-memory dependencies into the memory tools
        for tool in tools:
            if representation is not None and hasattr(tool, "set_representation"):
                tool.set_representation(store=representation)
            if hasattr(tool, "set_dialectic"):
                tool.set_dialectic(provider=self._provider, model=reasoning_model)
            if hasattr(tool, "set_docs_index"):
                tool.set_docs_index(index=docs_index)
            if skills_index is not None and hasattr(tool, "set_skills_index"):
                tool.set_skills_index(index=skills_index)

        logger.info("Discovered %d tools", len(tools))

        self._orchestrator = Orchestrator(
            provider=self._provider,
            model=self._model,
            tools=tools,
            workspace_dir=workspace_dir,
            think=think,
            deriver=deriver,
            representation=representation,
            memory_config=mem_cfg,
            skills_index=skills_index,
            skills_config=skills_cfg,
        )

        logger.info("Orchestrator initialized (model=%s, think=%s)", self._model, think)

        # Create agent manager and wire it into the agent tools
        self._agent_manager = AgentManager(config=self._config, main_handler_factory=None, bot_user_ids=self._bot_user_ids)
        self._agent_manager.register_main_orchestrator(orchestrator=self._orchestrator, sessions=self._sessions)

        main_name: str = self._config["main-agent"].get("name", "Memtrix")
        for tool in tools:
            if hasattr(tool, "set_agent_manager"):
                tool.set_agent_manager(manager=self._agent_manager)
            if hasattr(tool, "set_caller_name"):
                tool.set_caller_name(name=main_name)

        # Load existing sessions from config
        sessions_map: dict[str, str] = self._config.get("main-agent", {}).get("sessions", {})
        for room_id, session_id in sessions_map.items():
            self._sessions[room_id] = Session(sessions_dir=self._sessions_dir, session_id=session_id)

    def _seed_bot_user_ids(self) -> None:
        """
        This function populates the shared bot user IDs set from config.
        Called once at startup before channels are created.
        """
        # Main agent
        channel_name: str = self._config["main-agent"]["channel"]
        main_user_id: str = self._config["channels"][channel_name].get("user_id", "")
        if main_user_id:
            self._bot_user_ids.add(main_user_id)

        # Sub-agents
        for agent_config in self._config.get("agents", {}).values():
            agent_user_id: str = agent_config.get("matrix_user_id", "")
            if agent_user_id:
                self._bot_user_ids.add(agent_user_id)

    def _save_sessions(self) -> None:
        """
        This function persists the sessions mapping to config without overwriting secret placeholders.
        """
        sessions: Any = self._config["main-agent"]["sessions"]

        def mutate(disk_config: dict[str, Any]) -> None:
            disk_config["main-agent"]["sessions"] = sessions

        update_config(mutate=mutate)

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

        # Clear all inter-agent sessions where the main agent is the caller
        if self._agent_manager:
            self._agent_manager.clear_internal_sessions(agent_key="main")

        return "Session cleared."

    def _handle(self, user_input: str, room_id: str, notify: Callable[[str], None], send_file: Callable[[str], None] | None = None, ask: Callable[[str], str] | None = None, react: Callable[[str], None] | None = None) -> str:
        """
        This function handles a user message and returns the response.
        The notify callback sends real-time status messages to the channel.
        The send_file callback sends a file to the channel.
        """
        # Extract the raw body (strip channel header if present)
        raw_body: str = user_input.split(sep="\n", maxsplit=1)[1] if user_input.startswith("[Channel:") else user_input

        # Handle /clear — needs access to sessions and room_id
        if raw_body.strip().lower() in ("/clear", "/new"):
            return self._clear_session(room_id=room_id)

        # Check for slash commands
        if self._commands.is_command(message=raw_body):
            return self._commands.execute(message=raw_body)

        # Resolve per-call callbacks based on current command state
        notify_cb: Callable[[str], None] | None = notify if self._commands.verbose else None
        reasoning_cb: Callable[[str], None] | None = notify if self._commands.reasoning else None

        # Get the session for this room and run the orchestrator
        session: Session = self._get_session(room_id=room_id)
        return self._orchestrator.run(
            user_message=user_input,
            session=session,
            room_id=room_id,
            notify=notify_cb,
            notify_reasoning=reasoning_cb,
            send_file=send_file,
            ask=ask,
            react=react
        )

    def run(self) -> None:
        """
        This function starts Memtrix on the configured channel.
        """
        # Install signal handlers and start the liveness heartbeat so the web
        # control panel can supervise this process
        install_signal_handlers(on_shutdown=self._shutdown)
        start_heartbeat()

        # Load the configured provider and orchestrator
        logger.info("Loading provider and orchestrator...")
        self._load_provider()

        # Seed bot user IDs from config before starting any channels
        self._seed_bot_user_ids()

        # Boot all registered sub-agents
        if self._agent_manager:
            self._agent_manager.boot_all()

        # Resolve channel from config
        channel_instance: str = self._config["main-agent"]["channel"]
        channel_config: dict[str, Any] = self._config["channels"][channel_instance]
        channel_type: str = channel_config["type"]

        # Start the appropriate channel
        workspace_dir: str = self._config["workspace-directory"]
        attachments_dir: str = os.path.join(workspace_dir, "attachments")

        if channel_type == "matrix":
            agent_name: str = self._config["main-agent"].get("name", "Memtrix")
            logger.info("Starting Matrix channel as '%s'", agent_name)
            channel: MatrixChannel = MatrixChannel(
                homeserver=channel_config["homeserver"],
                user_id=channel_config["user_id"],
                access_token=channel_config["access_token"],
                display_name=channel_config.get("display_name", f"{agent_name} ⚡"),
                attachments_dir=attachments_dir,
                bot_user_ids=self._bot_user_ids
            )
            channel.run(handler=self._handle)
        else:
            logger.info("Starting CLI channel")
            cli: CLIChannel = CLIChannel()
            cli.run(handler=self._handle)

    def _cmd_consolidate(self, args: list[str]) -> str:
        """
        This function manually triggers a memory-consolidation pass, distilling the
        accumulated reasoning conclusions into a smaller, cleaner set.
        """
        if self._deriver is None:
            return "Reasoning memory is not enabled."

        results: dict[str, tuple[int, int]] = self._deriver.consolidate_all()
        if not results:
            return "Memory consolidation is paused or unavailable right now."

        parts: list[str] = []
        for peer, (removed, added) in results.items():
            if removed or added:
                parts.append(f"{peer}: {removed} -> {added} conclusions")
            else:
                parts.append(f"{peer}: not enough to distill yet")
        return "Memory consolidated. " + "; ".join(parts)

    def _shutdown(self) -> None:
        """
        This function performs a best-effort graceful shutdown: it flushes any
        pending reasoning work so nothing is lost across a restart.
        """
        if self._deriver is not None:
            try:
                self._deriver.flush_now()
            except Exception as exc:
                logger.error("Error flushing deriver on shutdown: %s", exc)

        try:
            SSHManager.get_instance().disconnect_all()
        except Exception as exc:
            logger.error("Error closing SSH sessions on shutdown: %s", exc)

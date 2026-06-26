#!/usr/bin/python3

import importlib
import logging
import os
import threading
from types import ModuleType
from typing import Any, Callable

from src.agents.manager import AgentManager
from src.channels.cli import CLIChannel
from src.channels.matrix import MatrixChannel
from src.core.commands import Commands
from src.core.config import CONFIG_PATH, resolve_agent_config, resolve_email_config, resolve_git_config, resolve_prompt_guard_config, resolve_skills_config, resolve_ssh_config, resolve_voice_config, resolve_workers_config, update_config
from src.integrations.prompt_guard import PromptGuard
from src.memory.deriver import Deriver
from src.indexing.docs import DocsIndex
from src.core.lifecycle import install_signal_handlers, start_heartbeat
from src.memory.index import ConversationIndex
from src.agents.orchestrator import Orchestrator
from src.agents.worker import WorkerManager
from src.providers.base import BaseProvider
from src.memory.store import RepresentationStore, resolve_memory_config
from src.core.session import Session
from src.indexing.skills import SKILL_TOOL_FILES, SkillsCatalog
from src.integrations.ssh import SSH_TOOL_FILES, SSHManager
from src.integrations.mail import MAIL_TOOL_FILES, EmailManager
from src.integrations.transcription import LocalSpeechToText
from src.tools import discover_tools
from src.tools.base import BaseTool

logger: logging.Logger = logging.getLogger(__name__)

# Reasoning-memory tool files, gated behind recall_mode
MEMORY_TOOL_FILES: set[str] = {
    "memory_profile_tool.py",
    "memory_search_tool.py",
    "memory_context_tool.py",
    "memory_conclude_tool.py",
}

# Tool files never given to background worker agents: agent management and worker
# spawning (no recursion), channel-bound interaction (workers have no live user or
# Matrix identity), and the core persona/memory files (workers keep no memory).
# The reasoning-memory, SSH, email and skill tool groups are excluded separately
# via their own file-group sets.
WORKER_EXCLUDED_TOOL_FILES: set[str] = {
    "create_agent_tool.py",
    "delete_agent_tool.py",
    "ask_agent_tool.py",
    "list_agents_tool.py",
    "spawn_worker_tool.py",
    "send_file_tool.py",
    "react_tool.py",
    "core_file_tools.py",
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

        # Background worker-agent manager (ephemeral task runners)
        self._worker_manager: WorkerManager | None = None

        # Active channel reference, set once the channel is created in run(). Used to
        # deliver background worker results into the originating room.
        self._channel: Any = None

        # Per-room locks serializing main-agent runs so a worker-result delivery
        # never races a live user turn on the same session.
        self._room_locks: dict[str, threading.Lock] = {}
        self._room_locks_guard: threading.Lock = threading.Lock()

        # Per-room sessions keyed by room id
        self._sessions: dict[str, Session] = {}

        # Per-room stop requests — room IDs for which /stop was sent
        self._stop_requested: set[str] = set()

        # Shared mutable set of all bot user IDs (main + sub-agents)
        self._bot_user_ids: set[str] = set()

        # Sessions directory
        self._sessions_dir: str = os.path.join(os.path.dirname(CONFIG_PATH), "sessions")

    def _sync_agent_template(self, workspace_dir: str) -> None:
        """
        This function refreshes the workspace AGENT.md from the bundled static template
        so that updated agent instructions take effect on restart. AGENT.md is a
        read-only system-prompt template (no tool can write it), so overwriting it is
        safe; the agent's chosen name is re-applied to preserve the persona. The
        mutable persona/memory files (BEHAVIOR.md, SOUL.md, USER.md) are
        never touched here.
        """
        static_agent_md: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "AGENT.md")
        workspace_agent_md: str = os.path.join(workspace_dir, "AGENT.md")
        try:
            with open(file=static_agent_md, mode="r", encoding="utf-8") as f:
                content: str = f.read()
        except OSError as e:
            logger.warning("Could not read static AGENT.md template: %s", e)
            return

        agent_name: str = self._config["main-agent"].get("name", "Memtrix")
        if agent_name != "Memtrix":
            content = content.replace(
                "You are **Memtrix**, a personal AI assistant.",
                f"You are **{agent_name}**, a personal AI assistant."
            )

        try:
            with open(file=workspace_agent_md, mode="w", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            logger.warning("Could not refresh workspace AGENT.md: %s", e)

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
        vision: bool = model_config.get("vision", False)

        # Refresh the AGENT.md system-prompt template from the bundled static copy so
        # instruction updates ship on restart. AGENT.md is not agent-editable, so it is
        # safe to overwrite; the mutable persona/memory cards are left untouched.
        self._sync_agent_template(workspace_dir=workspace_dir)

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

        # Exclude the email tools unless mailbox access is enabled
        email_cfg: dict[str, Any] = resolve_email_config(config=self._config)
        email_manager: EmailManager | None = None
        if not email_cfg["enabled"]:
            tool_exclude |= MAIL_TOOL_FILES
        else:
            email_manager = EmailManager(config=email_cfg)
            logger.info("Email access enabled (imap=%s, smtp=%s)", email_cfg["imap_host"], email_cfg["smtp_host"])

        # Resolve git HTTPS credentials (token comes from the GIT_TOKEN secret).
        git_cfg: dict[str, Any] = resolve_git_config(config=self._config)

        # Exclude the skill management tool unless the skills feature is enabled
        skills_cfg: dict[str, Any] = resolve_skills_config(config=self._config)
        if not skills_cfg["enabled"]:
            tool_exclude |= SKILL_TOOL_FILES

        # Exclude the spawn_worker tool unless background workers are enabled
        workers_cfg: dict[str, Any] = resolve_workers_config(config=self._config)
        if not workers_cfg["enabled"]:
            tool_exclude |= {"spawn_worker_tool.py"}

        # Core agent-loop settings (e.g. max tool-call rounds per request)
        agent_cfg: dict[str, Any] = resolve_agent_config(config=self._config)

        tools: list[BaseTool] = discover_tools(workspace_dir=workspace_dir, exclude=tool_exclude)

        # Eagerly initialize the conversation index so past sessions are searchable
        conversation_index: ConversationIndex = ConversationIndex.get_instance(workspace_dir=workspace_dir)
        conversation_index.start_periodic_sync()

        # Initialize the documentation index so the agent can research its own docs
        docs_index: DocsIndex = DocsIndex.get_instance()
        docs_index.start_periodic_sync()

        # Initialize the skills catalog so the agent can author and reuse skills
        skills_catalog: SkillsCatalog | None = None
        if skills_cfg["enabled"]:
            skills_catalog = SkillsCatalog.get_instance(workspace_dir=workspace_dir)
            logger.info("Skills enabled")

        # Wire reasoning-memory dependencies into the memory tools
        for tool in tools:
            if representation is not None and hasattr(tool, "set_representation"):
                tool.set_representation(store=representation)
            if hasattr(tool, "set_dialectic"):
                tool.set_dialectic(provider=self._provider, model=reasoning_model)
            if hasattr(tool, "set_docs_index"):
                tool.set_docs_index(index=docs_index)
            if skills_catalog is not None and hasattr(tool, "set_skills_catalog"):
                tool.set_skills_catalog(catalog=skills_catalog)
            if email_manager is not None and hasattr(tool, "set_email_manager"):
                tool.set_email_manager(manager=email_manager)
            if hasattr(tool, "set_git_credentials"):
                tool.set_git_credentials(token=git_cfg["token"], username=git_cfg["username"])

        logger.info("Discovered %d tools", len(tools))

        # Prompt-injection screening for untrusted tool output (web pages, search
        # results, remote command output, untrusted files). The classifier is loaded
        # lazily on a background thread so it never blocks startup; obtaining the
        # shared singleton here is cheap.
        pg_cfg: dict[str, Any] = resolve_prompt_guard_config(config=self._config)
        prompt_guard: PromptGuard | None = None
        if pg_cfg["enabled"]:
            model_dir: str = os.path.join(os.path.dirname(CONFIG_PATH), "models")
            prompt_guard = PromptGuard.get_instance(model_dir=model_dir, config=pg_cfg)
            threading.Thread(target=prompt_guard.warm_up, name="prompt-guard-warmup", daemon=True).start()
            logger.info("Prompt-injection screening enabled (Llama Prompt Guard 2, model=%s)", pg_cfg["model"])

            # Tools that screen their own untrusted content (e.g. email_check screens
            # each message body individually) get the shared screener injected here.
            for tool in tools:
                if hasattr(tool, "set_prompt_guard"):
                    tool.set_prompt_guard(guard=prompt_guard, fail_closed=pg_cfg["fail_closed"])

        self._orchestrator = Orchestrator(
            provider=self._provider,
            model=self._model,
            tools=tools,
            workspace_dir=workspace_dir,
            think=think,
            vision=vision,
            deriver=deriver,
            representation=representation,
            memory_config=mem_cfg,
            skills_catalog=skills_catalog,
            prompt_guard=prompt_guard,
            prompt_guard_fail_closed=pg_cfg["fail_closed"],
            max_iterations=agent_cfg["max_iterations"],
            max_history=agent_cfg["max_history"],
        )

        logger.info("Orchestrator initialized (model=%s, think=%s, vision=%s)", self._model, think, vision)

        # Background worker agents: ephemeral, memory-less task runners the main agent
        # can spawn to work autonomously without blocking the conversation. They reuse
        # the provider/model but get a fresh, restricted toolset (no agent management,
        # reasoning memory, SSH, email, skills, core files, send_file, react, or nested
        # workers) and no reasoning-memory or skills layers, so they keep nothing
        # persistent. A WorkerWatcher thread fires _handle_worker_result on completion.
        if workers_cfg["enabled"]:
            worker_exclude: set[str] = (
                WORKER_EXCLUDED_TOOL_FILES | MEMORY_TOOL_FILES | SSH_TOOL_FILES
                | MAIL_TOOL_FILES | SKILL_TOOL_FILES
            )
            worker_tools: list[BaseTool] = discover_tools(workspace_dir=workspace_dir, exclude=worker_exclude)
            for tool in worker_tools:
                if hasattr(tool, "set_docs_index"):
                    tool.set_docs_index(index=docs_index)
                if hasattr(tool, "set_git_credentials"):
                    tool.set_git_credentials(token=git_cfg["token"], username=git_cfg["username"])
                if prompt_guard is not None and hasattr(tool, "set_prompt_guard"):
                    tool.set_prompt_guard(guard=prompt_guard, fail_closed=pg_cfg["fail_closed"])
            worker_orchestrator: Orchestrator = Orchestrator(
                provider=self._provider,
                model=self._model,
                tools=worker_tools,
                workspace_dir=workspace_dir,
                think=think,
                vision=vision,
                deriver=None,
                representation=None,
                memory_config=None,
                skills_catalog=None,
                prompt_guard=prompt_guard,
                prompt_guard_fail_closed=pg_cfg["fail_closed"],
                max_iterations=agent_cfg["max_iterations"],
                max_history=agent_cfg["max_history"],
            )
            self._worker_manager = WorkerManager(
                orchestrator=worker_orchestrator,
                sessions_dir=self._sessions_dir,
                trigger=self._handle_worker_result,
                max_concurrent=int(workers_cfg["max_concurrent"]),
            )
            self._worker_manager.start()
            logger.info("Background worker agents enabled (max_concurrent=%d, tools=%d)", workers_cfg["max_concurrent"], len(worker_tools))

        # Create agent manager and wire it into the agent tools
        self._agent_manager = AgentManager(config=self._config, bot_user_ids=self._bot_user_ids)
        self._agent_manager.register_main_orchestrator(orchestrator=self._orchestrator, sessions=self._sessions)

        main_name: str = self._config["main-agent"].get("name", "Memtrix")
        for tool in tools:
            if hasattr(tool, "set_agent_manager"):
                tool.set_agent_manager(manager=self._agent_manager)
            if self._worker_manager is not None and hasattr(tool, "set_worker_manager"):
                tool.set_worker_manager(manager=self._worker_manager)
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

    def _get_room_lock(self, room_id: str) -> threading.Lock:
        """
        This function returns the lock serializing main-agent runs for a room,
        creating it on first use.
        """
        with self._room_locks_guard:
            lock: threading.Lock | None = self._room_locks.get(room_id)
            if lock is None:
                lock = threading.Lock()
                self._room_locks[room_id] = lock
            return lock

    def _handle_worker_result(self, room_id: str, worker_id: str, task: str, result: str, ok: bool) -> None:
        """
        This function is the trigger fired by the WorkerWatcher when a background
        worker finishes. It runs the main agent with a synthetic notification message
        carrying the worker's result so the agent can relay the outcome to the user in
        the originating room — a pure in-process call, no polling or event bus. The
        actual run is dispatched on its own daemon thread (so the single watcher thread
        is never blocked) and serialized per room via the room lock (so it never races
        a live user turn on the same session).
        """
        def deliver() -> None:
            with self._get_room_lock(room_id=room_id):
                try:
                    session: Session = self._get_session(room_id=room_id)
                    status: str = "completed successfully" if ok else "failed"
                    synthetic: str = (
                        "[System notification — not from the user]\n"
                        f"A background worker you previously spawned (id {worker_id}) has {status}.\n"
                        f"Its task was:\n{task}\n\n"
                        f"Worker result:\n{result}\n\n"
                        "Relay the outcome to the user now in a natural, concise way. "
                        "Continue any follow-up work if appropriate."
                    )
                    notify_cb: Callable[[str], None] | None = None
                    if self._channel is not None and self._commands.verbose:
                        notify_cb = lambda msg: self._channel.send_to_room(room_id=room_id, body=msg, notice=True)
                    response: str = self._orchestrator.run(
                        user_message=synthetic,
                        session=session,
                        room_id=room_id,
                        notify=notify_cb,
                        agent_depth=0,
                    )
                    if self._channel is not None and response.strip():
                        self._channel.send_to_room(room_id=room_id, body=response)
                except Exception as e:
                    logger.error("Failed to deliver worker %s result to %s: %s", worker_id, room_id, e, exc_info=True)

        threading.Thread(target=deliver, name=f"worker-deliver-{worker_id}", daemon=True).start()

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

        # Handle /stop — interrupt the current run without affecting the session
        if raw_body.strip().lower() == "/stop":
            self._stop_requested.add(room_id)
            return "⏹️ Stopped."

        # Check for slash commands
        if self._commands.is_command(message=raw_body):
            return self._commands.execute(message=raw_body)

        # Resolve per-call callbacks based on current command state
        notify_cb: Callable[[str], None] | None = notify if self._commands.verbose else None
        reasoning_cb: Callable[[str], None] | None = notify if self._commands.reasoning else None

        # Check if a stop was requested for this room, and clear it before running
        if room_id in self._stop_requested:
            self._stop_requested.discard(room_id)
            return "(stopped)"

        # Create a callable to check if stop is requested during the run
        def should_stop() -> bool:
            return room_id in self._stop_requested

        # Get the session for this room and run the orchestrator. The per-room lock
        # serializes runs so a background worker-result delivery can never race a live
        # user turn on the same session.
        session: Session = self._get_session(room_id=room_id)
        with self._get_room_lock(room_id=room_id):
            result = self._orchestrator.run(
                user_message=user_input,
                session=session,
                room_id=room_id,
                notify=notify_cb,
                notify_reasoning=reasoning_cb,
                send_file=send_file,
                ask=ask,
                react=react,
                should_stop=should_stop,
            )
        # Clear the stop flag in case it was set during the run
        self._stop_requested.discard(room_id)
        return result

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
            voice_cfg: dict[str, Any] = resolve_voice_config(config=self._config)
            transcriber: LocalSpeechToText | None = None
            if voice_cfg.get("enabled") and voice_cfg.get("provider") == "local":
                transcriber = LocalSpeechToText(model_name=str(voice_cfg.get("model", "base")))
            logger.info("Starting Matrix channel as '%s'", agent_name)
            channel: MatrixChannel = MatrixChannel(
                homeserver=channel_config["homeserver"],
                user_id=channel_config["user_id"],
                access_token=channel_config["access_token"],
                display_name=channel_config.get("display_name", f"{agent_name} ⚡"),
                attachments_dir=attachments_dir,
                bot_user_ids=self._bot_user_ids,
                voice_config=voice_cfg,
                transcriber=transcriber,
            )
            self._channel = channel
            channel.run(handler=self._handle)
        else:
            logger.info("Starting CLI channel")
            cli: CLIChannel = CLIChannel()
            self._channel = cli
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

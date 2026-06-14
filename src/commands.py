#!/usr/bin/python3

from typing import Any, Callable

from src.config import update_config
from src.usage import format_costs


class Commands:

    def __init__(self, agent_config: dict[str, Any], config_path: list[str], providers: dict[str, Any] | None = None) -> None:
        """
        This is the Commands class which handles slash-commands.
        agent_config is the agent's own config section (e.g. config["main-agent"] or config["agents"]["dave"]).
        config_path is the key path to that section in config.json (e.g. ["main-agent"] or ["agents", "dave"]).
        providers is the resolved config["providers"] map, used by /costs.
        """
        # Registry of command handlers
        self._commands: dict[str, Callable[[list[str]], str]] = {}

        # Key path to the agent's config section for persistence
        self._config_path: list[str] = config_path

        # Resolved providers map (for cost reporting)
        self._providers: dict[str, Any] = providers or {}

        # Register built-in commands
        self._register_builtins()

        # Load verbose state from agent config
        self.verbose: bool = agent_config.get("verbose", False)

        # Load reasoning state from agent config
        self.reasoning: bool = agent_config.get("reasoning", False)

    def _register_builtins(self) -> None:
        """
        This function registers the built-in slash commands.
        """
        self._commands["clear"] = self._cmd_clear
        self._commands["new"] = self._cmd_clear
        self._commands["verbose"] = self._cmd_verbose
        self._commands["reasoning"] = self._cmd_reasoning
        self._commands["help"] = self._cmd_help

        # /costs only makes sense when at least one OpenRouter provider is configured
        if any(isinstance(p, dict) and p.get("type") == "openrouter" for p in self._providers.values()):
            self._commands["costs"] = self._cmd_costs

    def register(self, name: str, handler: Callable[[list[str]], str]) -> None:
        """
        This function registers a new slash command.
        """
        self._commands[name] = handler

    def is_command(self, message: str) -> bool:
        """
        This function checks if a message is a slash command.
        """
        return message.startswith("/")

    def execute(self, message: str) -> str:
        """
        This function parses and executes a slash command.
        """
        # Split into command name and arguments
        parts: list[str] = message[1:].strip().split()
        if not parts:
            return "Unknown command. Type /help for a list."

        name: str = parts[0].lower()
        args: list[str] = parts[1:]

        if name in self._commands:
            return self._commands[name](args)

        return f"Unknown command: /{name}. Type /help for a list."

    def _save_setting(self, key: str, value: Any) -> None:
        """
        This function persists a setting to the agent's config section.
        """
        def mutate(config: dict[str, Any]) -> None:
            section: dict[str, Any] = config
            for part in self._config_path:
                section = section[part]
            section[key] = value

        update_config(mutate=mutate)

    def _cmd_verbose(self, args: list[str]) -> str:
        """
        This function toggles verbose mode.
        """
        if not args:
            status: str = "on" if self.verbose else "off"
            return f"Verbose mode is {status}."

        if args[0].lower() == "on":
            self.verbose = True
            self._save_setting(key="verbose", value=True)
            return "Verbose mode enabled."
        elif args[0].lower() == "off":
            self.verbose = False
            self._save_setting(key="verbose", value=False)
            return "Verbose mode disabled."

        return "Usage: /verbose on|off"

    def _cmd_reasoning(self, args: list[str]) -> str:
        """
        This function toggles reasoning display.
        """
        if not args:
            status: str = "on" if self.reasoning else "off"
            return f"Reasoning display is {status}."

        if args[0].lower() == "on":
            self.reasoning = True
            self._save_setting(key="reasoning", value=True)
            return "Reasoning display enabled."
        elif args[0].lower() == "off":
            self.reasoning = False
            self._save_setting(key="reasoning", value=False)
            return "Reasoning display disabled."

        return "Usage: /reasoning on|off"

    def _cmd_clear(self, args: list[str]) -> str:
        """
        This function is a placeholder — /clear is handled by Memtrix directly.
        """
        return "Session cleared."

    def _cmd_costs(self, args: list[str]) -> str:
        """
        This function reports OpenRouter credit usage for the configured providers,
        including credits used today (current UTC day). Credits are US dollars.
        """
        return format_costs(providers=self._providers)

    def _cmd_help(self, args: list[str]) -> str:
        """
        This function lists available commands.
        """
        names: list[str] = sorted(self._commands.keys())
        return "Available commands: " + ", ".join(f"/{n}" for n in names)

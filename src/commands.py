#!/usr/bin/python3

import json
from typing import Any, Callable

from src.config import CONFIG_PATH


class Commands:

    def __init__(self, config: dict[str, Any]) -> None:
        """
        This is the Commands class which handles slash-commands.
        """
        # Registry of command handlers
        self._commands: dict[str, Callable[[list[str]], str]] = {}

        # Register built-in commands
        self._register_builtins()

        # Load verbose state from config
        self.verbose: bool = config.get("main-agent", {}).get("verbose", False)

        # Load reasoning state from config
        self.reasoning: bool = config.get("main-agent", {}).get("reasoning", False)

    def _register_builtins(self) -> None:
        """
        This function registers the built-in slash commands.
        """
        self._commands["clear"] = self._cmd_clear
        self._commands["verbose"] = self._cmd_verbose
        self._commands["reasoning"] = self._cmd_reasoning
        self._commands["help"] = self._cmd_help

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
        This function persists a main-agent setting to config.
        """
        with open(file=CONFIG_PATH, mode="r") as f:
            config: dict[str, Any] = json.load(fp=f)
        config["main-agent"][key] = value
        with open(file=CONFIG_PATH, mode="w") as f:
            json.dump(obj=config, fp=f, indent=4)

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

    def _cmd_help(self, args: list[str]) -> str:
        """
        This function lists available commands.
        """
        names: list[str] = sorted(self._commands.keys())
        return "Available commands: " + ", ".join(f"/{n}" for n in names)

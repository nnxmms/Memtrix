#!/usr/bin/python3

import importlib
from types import ModuleType
from typing import Any

from src.channels.cli import CLIChannel
from src.channels.matrix import MatrixChannel
from src.providers.base import BaseProvider


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
                return

        raise RuntimeError(f"No provider class found for type '{provider_type}'.")

    def _handle(self, user_input: str) -> str:
        """
        This function handles a user message and returns the provider's response.
        """
        # Build a single-turn history
        history: list[dict[str, str]] = [{"role": "user", "content": user_input}]
        return self._provider.completions(model=self._model, history=history)

    def run(self) -> None:
        """
        This function starts Memtrix on the configured channel.
        """
        # Load the configured provider
        self._load_provider()

        # Resolve channel from config
        channel_instance: str = self._config["main-agent"]["channel"]
        channel_config: dict[str, Any] = self._config["channels"][channel_instance]
        channel_type: str = channel_config["type"]

        # Start the appropriate channel
        if channel_type == "matrix":
            channel: MatrixChannel = MatrixChannel(
                homeserver=channel_config["homeserver"],
                user_id=channel_config["user_id"],
                access_token=channel_config["access_token"]
            )
            channel.run(handler=self._handle)
        else:
            cli: CLIChannel = CLIChannel()
            cli.run(handler=self._handle)

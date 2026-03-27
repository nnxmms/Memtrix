#!/usr/bin/python3

import json
import logging
import os
import sys
from typing import Any

from src import __version__
from src.config import CONFIG_PATH
from src.memtrix import Memtrix
from src.secrets import clear_secrets_from_env, resolve_secrets

logger: logging.Logger = logging.getLogger(__name__)


def main() -> None:
    """
    This is the main entry point for Memtrix.
    """
    # Configure logging for the entire application
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout
    )

    # Suppress noisy third-party loggers
    for name in ("httpx", "urllib3", "httpcore", "chromadb", "sentence_transformers",
                 "nio", "peewee", "huggingface_hub", "transformers_modules"):
        logging.getLogger(name).setLevel(logging.WARNING)

    logger.info("Memtrix v%s starting", __version__)

    # Ensure setup has been run
    if not os.path.exists(path=CONFIG_PATH):
        logger.error("Config not found at %s — run ./setup.sh first", CONFIG_PATH)
        sys.exit(1)

    # Load and validate config
    with open(file=CONFIG_PATH, mode="r") as f:
        config: dict[str, Any] = json.load(fp=f)

    # Resolve $PLACEHOLDER secrets from environment variables
    config: dict[str, Any] = resolve_secrets(config=config)

    # Clear secrets from environment so they can't be leaked via `env` or /proc
    clear_secrets_from_env()

    # Ensure onboarding has been completed
    required_sections: list[str] = ["providers", "models", "channels"]
    required_agent_keys: list[str] = ["model", "channel"]
    if not all(config.get(s) for s in required_sections):
        logger.error("Onboarding not completed — run ./onboard.sh first")
        sys.exit(1)
    if not all(config["main-agent"].get(k) for k in required_agent_keys):
        logger.error("Onboarding not completed — run ./onboard.sh first")
        sys.exit(1)

    # Start Memtrix
    memtrix: Memtrix = Memtrix(config=config)
    memtrix.run()


if __name__ == "__main__":
    main()


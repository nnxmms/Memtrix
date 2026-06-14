#!/usr/bin/python3

import logging
import os
import sys

import uvicorn

logger: logging.Logger = logging.getLogger(__name__)


def main() -> None:
    """
    This function starts the Memtrix Web Control Panel with uvicorn.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    for name in ("httpx", "urllib3", "httpcore", "chromadb", "sentence_transformers",
                 "huggingface_hub", "transformers_modules"):
        logging.getLogger(name).setLevel(logging.WARNING)

    # Bind to all interfaces inside the container; operators must front it with an
    # authenticating reverse proxy and keep the port off the public host.
    host: str = os.environ.get("MEMTRIX_WEB_HOST", "0.0.0.0")
    port: int = int(os.environ.get("MEMTRIX_WEB_PORT", "8800"))

    logger.info("Starting Memtrix Control Panel on %s:%d", host, port)
    uvicorn.run("src.web.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

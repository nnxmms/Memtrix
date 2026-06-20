#!/usr/bin/python3

import logging
import os
from typing import Any

from fastapi import Header, HTTPException, status

from src.core.config import load_config
from src.memory.store import RepresentationStore

logger: logging.Logger = logging.getLogger(__name__)

# Optional shared secret. When set, every API request must send it in the
# X-Memtrix-Token header. This is defense-in-depth; operators are expected to
# also front the panel with their own authenticating reverse proxy.
WEB_TOKEN_ENV: str = "MEMTRIX_WEB_TOKEN"


def get_workspace_dir() -> str:
    """
    This function returns the main agent's workspace directory from config.
    """
    config: dict[str, Any] = load_config()
    return config.get("workspace-directory", "/home/memtrix/workspace")


def get_store() -> RepresentationStore:
    """
    This function returns the shared RepresentationStore for the main workspace.
    With CHROMA_URL set it connects to the shared ChromaDB server so reads and
    writes coordinate with the running agent.
    """
    return RepresentationStore.get_instance(workspace_dir=get_workspace_dir())


def require_token(x_memtrix_token: str | None = Header(default=None)) -> None:
    """
    This dependency enforces the optional shared-secret header when configured.
    """
    expected: str | None = os.environ.get(WEB_TOKEN_ENV)
    if not expected:
        return
    if x_memtrix_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Memtrix-Token header.",
        )

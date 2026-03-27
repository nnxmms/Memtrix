#!/usr/bin/python3

from typing import Any


from ipaddress import IPv4Address, IPv6Address


import importlib
import inspect
import ipaddress
import os
import socket
from types import ModuleType
from urllib.parse import urlparse

from src.tools.base import BaseTool


def discover_tools(workspace_dir: str, exclude: set[str] | None = None) -> list[BaseTool]:
    """
    This function discovers and instantiates all tools in the tools directory.
    Optionally excludes tool files by filename.
    """
    tools: list[BaseTool] = []

    # Directory of this file
    tools_dir: str = os.path.dirname(__file__)

    # Excluded files that are not tool implementations
    excluded: set[str] = {"__init__.py", "base.py", "utils.py"}
    if exclude:
        excluded: set[str] = excluded | exclude

    for filename in sorted(os.listdir(tools_dir)):
        if not filename.endswith(".py") or filename in excluded:
            continue

        # Dynamically import the module
        module_name: str = f"src.tools.{filename[:-3]}"
        module: ModuleType = importlib.import_module(name=module_name)

        # Find all classes that inherit from BaseTool
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseTool) and obj is not BaseTool:
                tools.append(obj(workspace_dir=workspace_dir))

    return tools


# Internal hostnames that must not be accessed by tools
BLOCKED_HOSTNAMES: set[str] = {"conduit", "searxng", "memtrix", "localhost", "host.docker.internal"}


def validate_url_not_internal(url: str) -> str | None:
    """
    Validates that a URL does not target internal/private network addresses.
    Returns an error message if blocked, None if the URL is safe.
    """
    parsed = urlparse(url)
    hostname: str | None = parsed.hostname

    if not hostname:
        return "Error: could not parse hostname from URL."

    # Block known internal Docker service names
    if hostname.lower() in BLOCKED_HOSTNAMES:
        return "Error: requests to internal services are not allowed."

    # Resolve hostname and block private/reserved IPs
    try:
        for _, _, _, _, sockaddr in socket.getaddrinfo(host=hostname, port=None):
            ip: IPv4Address | IPv6Address = ipaddress.ip_address(address=sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return "Error: requests to private/internal network addresses are not allowed."
    except socket.gaierror:
        pass  # Let the actual request handle DNS failures

    return None


def confirm_with_user(kwargs: dict, message: str) -> bool:
    """
    Asks the user for yes/no confirmation via the human-in-the-loop callback.
    Returns False (deny) if no callback is available — prevents auto-approval
    during inter-agent calls where no human is in the loop.
    """
    ask: Any | None = kwargs.get("_ask")
    if not ask:
        return False

    prompt: str = message
    while True:
        try:
            answer: str = ask(prompt)
        except Exception:
            return False
        if answer.strip().lower() in ("yes", "y"):
            return True
        if answer.strip().lower() in ("no", "n"):
            return False
        prompt = "Please answer yes or no."

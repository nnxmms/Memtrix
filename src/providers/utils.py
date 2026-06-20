#!/usr/bin/python3

import importlib
import inspect
import logging
import os
import random
import time
from types import ModuleType
from typing import Any, Callable

from src.providers.base import BaseProvider

logger: logging.Logger = logging.getLogger(__name__)


def with_retries(fn: Callable[[], Any], *, attempts: int = 3, base_delay: float = 1.0,
                 max_delay: float = 10.0, label: str = "provider call") -> Any:
    """
    This function calls fn() and retries on any exception with exponential backoff
    and jitter, re-raising the last exception once attempts are exhausted. It makes
    a single transient network or rate-limit error non-fatal to an agent run.
    """
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if attempt == attempts - 1:
                break
            delay: float = min(max_delay, base_delay * (2 ** attempt)) + random.uniform(0, base_delay)
            logger.warning(
                "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                label, attempt + 1, attempts, e, delay,
            )
            time.sleep(delay)
    assert last_error is not None
    raise last_error


def get_requirements() -> dict[str, list[str]]:
    """
    This function returns the required init parameters for each provider.
    """
    requirements: dict[str, list[str]] = {}

    # Directory of this file
    providers_dir: str = os.path.dirname(__file__)

    # Excluded files that are not provider implementations
    excluded: set[str] = {"__init__.py", "base.py", "utils.py"}

    for filename in os.listdir(providers_dir):
        if not filename.endswith(".py") or filename in excluded:
            continue

        # Dynamically import the module
        module_name: str = f"src.providers.{filename[:-3]}"
        module: ModuleType = importlib.import_module(name=module_name)

        # Find the class that inherits from BaseProvider
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseProvider) and obj is not BaseProvider:
                # Collect __init__ params excluding 'self'
                params: list[str] = [
                    p for p in inspect.signature(obj=obj.__init__).parameters
                    if p != "self"
                ]
                requirements[filename[:-3]] = params
                break

    return requirements

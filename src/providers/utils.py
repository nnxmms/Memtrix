#!/usr/bin/python3

import importlib
import inspect
import os
from types import ModuleType

from src.providers.base import BaseProvider


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

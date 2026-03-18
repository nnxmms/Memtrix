#!/usr/bin/python3

import importlib
import inspect
import os
from types import ModuleType

from src.tools.base import BaseTool


def discover_tools(workspace_dir: str) -> list[BaseTool]:
    """
    This function discovers and instantiates all tools in the tools directory.
    """
    tools: list[BaseTool] = []

    # Directory of this file
    tools_dir: str = os.path.dirname(__file__)

    # Excluded files that are not tool implementations
    excluded: set[str] = {"__init__.py", "base.py", "utils.py"}

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

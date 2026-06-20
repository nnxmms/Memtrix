#!/usr/bin/python3

from datetime import datetime
from typing import Any

from src.tools.base import BaseTool


class CurrentTimeTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the CurrentTimeTool which returns the current date and time.
        """
        super().__init__(
            name="get_current_time",
            description="Get the current date and time.",
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function returns the current date and time.
        """
        return datetime.now().strftime(format="%Y-%m-%d %H:%M:%S")

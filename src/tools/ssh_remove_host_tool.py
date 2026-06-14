#!/usr/bin/python3

from typing import Any

from src.ssh_manager import SSHError, SSHManager
from src.tools.base import BaseTool


class SSHRemoveHostTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the SSHRemoveHostTool which unregisters a remote host.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="ssh_remove_host",
            description=(
                "Unregister a remote host by its alias. Any open session to that host is closed first. "
                "This only removes it from Memtrix's host list; it does not change anything on the host."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "alias": {
                        "type": "string",
                        "description": "The alias of the host to remove."
                    }
                },
                "required": ["alias"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function unregisters a remote host.
        """
        alias: str = str(kwargs.get("alias", "")).strip()
        if not alias:
            return "Error: alias cannot be empty."

        try:
            SSHManager.get_instance().remove_host(alias=alias)
        except SSHError as exc:
            return f"Error: {exc}"

        return f"Removed host '{alias}'."

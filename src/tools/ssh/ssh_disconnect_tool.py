#!/usr/bin/python3

from typing import Any

from src.integrations.ssh import SSHManager
from src.tools.base import BaseTool


class SSHDisconnectTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the SSHDisconnectTool which closes an open SSH session.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="ssh_disconnect",
            description=(
                "Close the open SSH session for a host (or all hosts). Any in-memory sudo password "
                "for that host is forgotten. The host stays registered and can be reconnected later."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "alias": {
                        "type": "string",
                        "description": "The alias of the host to disconnect. Omit or use 'all' to close every open session."
                    }
                },
                "required": []
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function closes one or all open SSH sessions.
        """
        alias: str = str(kwargs.get("alias", "")).strip()
        manager: SSHManager = SSHManager.get_instance()

        if not alias or alias.lower() == "all":
            manager.disconnect_all()
            return "Closed all open SSH sessions."

        closed: bool = manager.disconnect(alias=alias)
        if not closed:
            return f"No open session for '{alias}'."
        return f"Disconnected from '{alias}'."

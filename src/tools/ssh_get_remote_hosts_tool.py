#!/usr/bin/python3

from typing import Any

from src.ssh_manager import SSHManager
from src.tools.base import BaseTool


class SSHGetRemoteHostsTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the SSHGetRemoteHostsTool which lists registered remote hosts.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="ssh_get_remote_hosts",
            description=(
                "List the remote hosts registered for SSH administration, including whether each "
                "currently has an open session."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function lists the registered remote hosts.
        """
        hosts: list[dict[str, Any]] = SSHManager.get_instance().list_hosts()
        if not hosts:
            return "No remote hosts registered yet. Add one with ssh_add_host."

        lines: list[str] = ["Registered remote hosts:"]
        for host in hosts:
            status: str = "connected" if host["connected"] else "not connected"
            lines.append(
                f"- {host['alias']}: {host['username']}@{host['hostname']}:{host['port']} ({status})"
            )
        return "\n".join(lines)

#!/usr/bin/python3

from typing import Any

from src.integrations.ssh import SSHError, SSHManager
from src.tools.base import BaseTool
from src.tools.utils import confirm_with_user


class SSHConnectTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the SSHConnectTool which opens a persistent SSH session to a host.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="ssh_connect",
            description=(
                "Open a persistent interactive SSH session to a registered host (see ssh_add_host). "
                "The session stays open so later ssh_run calls share the same shell — the working "
                "directory and environment persist between commands, like a real terminal. On the "
                "first connection to a host its key is verified and you are asked to trust it. "
                "Close the session with ssh_disconnect when finished."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "alias": {
                        "type": "string",
                        "description": "The alias of the host to connect to."
                    }
                },
                "required": ["alias"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function opens a persistent SSH session to a registered host.
        """
        alias: str = str(kwargs.get("alias", "")).strip()
        if not alias:
            return "Error: alias cannot be empty."

        manager: SSHManager = SSHManager.get_instance()

        if manager.is_connected(alias=alias):
            return f"Already connected to '{alias}'."

        def confirm_host_key(key_type: str, fingerprint: str, target: str) -> bool:
            message: str = (
                f"First connection to {target}.\n\n"
                f"Host key type: {key_type}\n"
                f"Fingerprint:   {fingerprint}\n\n"
                "Verify this fingerprint matches the host, then trust and remember it? (yes/no)"
            )
            return confirm_with_user(kwargs, message=message)

        try:
            manager.connect(alias=alias, confirm_host_key=confirm_host_key)
        except SSHError as exc:
            return f"Error: {exc}"

        try:
            info, _ = manager.run(alias=alias, command="uname -a; whoami; pwd")
        except SSHError:
            info = ""

        result: str = f"Connected to '{alias}'. The session is open and ready for ssh_run."
        if info.strip():
            result += "\n\n" + info.strip()
        return result

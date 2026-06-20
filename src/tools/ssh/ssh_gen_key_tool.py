#!/usr/bin/python3

from typing import Any

from src.integrations.ssh import SSHError, SSHManager
from src.tools.base import BaseTool
from src.tools.utils import confirm_with_user


class SSHGenKeyTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the SSHGenKeyTool which generates the agent's ed25519 SSH keypair.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="ssh_gen_key",
            description=(
                "Generate Memtrix's own ed25519 SSH keypair used to log in to remote hosts. "
                "If a key already exists it is returned unchanged unless 'force' is true. "
                "Returns the PUBLIC key only — install it in a remote host's ~/.ssh/authorized_keys "
                "to grant access. The private key never leaves the agent."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "force": {
                        "type": "boolean",
                        "description": "Overwrite and replace an existing key. This invalidates the current key everywhere it is installed. Defaults to false."
                    }
                },
                "required": []
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function generates (or returns) the agent's SSH public key.
        """
        force: bool = bool(kwargs.get("force", False))
        manager: SSHManager = SSHManager.get_instance()

        existed: bool = manager.get_pub_key() is not None
        if existed and force:
            confirm_msg: str = (
                "Memtrix wants to REPLACE its existing SSH key.\n\n"
                "The current key will stop working on every host where it is installed, "
                "and the new public key must be re-installed everywhere.\n\n"
                "Allow this? (yes/no)"
            )
            if not confirm_with_user(kwargs, message=confirm_msg):
                return "Key generation denied by user. Existing key kept."

        try:
            pub: str = manager.gen_key(force=force)
        except SSHError as exc:
            return f"Error: {exc}"

        if existed and not force:
            return "An SSH key already exists. Public key:\n\n" + pub + "\n\nUse force=true to replace it."
        return "SSH key ready. Public key (install this in the remote host's authorized_keys):\n\n" + pub

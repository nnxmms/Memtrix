#!/usr/bin/python3

from typing import Any

from src.integrations.ssh import SSHManager
from src.tools.base import BaseTool


class SSHGetPubKeyTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the SSHGetPubKeyTool which returns the agent's SSH public key.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="ssh_get_pub_key",
            description=(
                "Return Memtrix's SSH public key so it can be installed in a remote host's "
                "~/.ssh/authorized_keys. If no key exists yet, generate one first with ssh_gen_key."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function returns the agent's SSH public key.
        """
        pub: str | None = SSHManager.get_instance().get_pub_key()
        if pub is None:
            return "No SSH key exists yet. Generate one first with ssh_gen_key."
        return "Public key (install this in the remote host's authorized_keys):\n\n" + pub

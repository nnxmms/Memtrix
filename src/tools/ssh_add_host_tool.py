#!/usr/bin/python3

from typing import Any

from src.ssh_manager import SSHError, SSHManager
from src.tools.base import BaseTool


class SSHAddHostTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the SSHAddHostTool which registers a remote host under an alias.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="ssh_add_host",
            description=(
                "Register a remote host so it can be managed over SSH. The host is saved under a "
                "short alias and reused by ssh_connect / ssh_run. Memtrix authenticates with its own "
                "key (ssh_gen_key), which must already be installed in the host's authorized_keys."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "alias": {
                        "type": "string",
                        "description": "A short name to refer to this host, e.g. 'webserver' or 'pi'. Letters, digits, '.', '_' and '-' only."
                    },
                    "hostname": {
                        "type": "string",
                        "description": "The hostname or IP address of the remote host."
                    },
                    "username": {
                        "type": "string",
                        "description": "The username to log in as on the remote host."
                    },
                    "port": {
                        "type": "integer",
                        "description": "The SSH port. Defaults to 22."
                    }
                },
                "required": ["alias", "hostname", "username"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function registers a remote host.
        """
        alias: str = str(kwargs.get("alias", "")).strip()
        hostname: str = str(kwargs.get("hostname", "")).strip()
        username: str = str(kwargs.get("username", "")).strip()
        port: int = int(kwargs.get("port", 22) or 22)

        try:
            SSHManager.get_instance().add_host(alias=alias, hostname=hostname, username=username, port=port)
        except SSHError as exc:
            return f"Error: {exc}"
        except (TypeError, ValueError):
            return "Error: port must be a number between 1 and 65535."

        return f"Registered host '{alias}' ({username}@{hostname}:{port}). Connect with ssh_connect."

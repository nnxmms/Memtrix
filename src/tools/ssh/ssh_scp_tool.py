#!/usr/bin/python3

import os
from typing import Any

from src.integrations.ssh import SSHError, SSHManager
from src.tools.base import BaseTool
from src.tools.utils import confirm_with_user

# Maximum size for a single SCP transfer: 100 MB.
MAX_SCP_BYTES: int = 100 * 1024 * 1024

# Default workspace subdirectory for files pulled down from a host.
DOWNLOADS_DIR: str = "downloads"


def _human_size(num_bytes: int) -> str:
    """
    This function formats a byte count as a short human-readable size.
    """
    size: float = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{num_bytes} B"


class SSHScpTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the SSHScpTool which copies a file between the local workspace and a
        connected remote host over SFTP, in either direction.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="ssh_scp",
            description=(
                "Copy a single file between the local workspace and a connected host (open the host "
                "first with ssh_connect). Set direction to 'upload' to send a workspace file to the "
                "host, or 'download' to pull a remote file into the workspace. Local paths are relative "
                "to the workspace; remote paths are absolute or relative to the login directory on the "
                "host. For uploads, if remote_path is a directory the filename is kept. For downloads, "
                "local_path is optional and defaults to downloads/<filename>. Max 100 MB per transfer."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "alias": {
                        "type": "string",
                        "description": "The alias of the connected host."
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["upload", "download"],
                        "description": "'upload' sends a workspace file to the host; 'download' pulls a remote file into the workspace."
                    },
                    "remote_path": {
                        "type": "string",
                        "description": "The path on the remote host (the destination for an upload, the source for a download)."
                    },
                    "local_path": {
                        "type": "string",
                        "description": "The workspace-relative path. Required for an upload (the source file); optional for a download (defaults to downloads/<filename>)."
                    }
                },
                "required": ["alias", "direction", "remote_path"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function copies a file to or from a connected host over SFTP.
        """
        alias: str = str(kwargs.get("alias", "")).strip()
        direction: str = str(kwargs.get("direction", "")).strip().lower()
        remote_path: str = str(kwargs.get("remote_path", "")).strip()
        local_path: str = str(kwargs.get("local_path", "")).strip()

        if not alias:
            return "Error: alias cannot be empty."
        if direction not in ("upload", "download"):
            return "Error: direction must be 'upload' or 'download'."
        if not remote_path:
            return "Error: remote_path cannot be empty."

        manager: SSHManager = SSHManager.get_instance()
        if not manager.is_connected(alias=alias):
            return f"Not connected to '{alias}'. Open a session first with ssh_connect."

        if direction == "upload":
            return self._upload(manager=manager, alias=alias, local_path=local_path, remote_path=remote_path, kwargs=kwargs)
        return self._download(manager=manager, alias=alias, local_path=local_path, remote_path=remote_path, kwargs=kwargs)

    def _within_workspace(self, full_path: str) -> bool:
        """
        This function reports whether a resolved path is inside the workspace.
        """
        workspace: str = os.path.realpath(self._workspace_dir)
        resolved: str = os.path.realpath(full_path)
        return resolved == workspace or resolved.startswith(workspace + os.sep)

    def _upload(self, manager: SSHManager, alias: str, local_path: str, remote_path: str, kwargs: dict[str, Any]) -> str:
        if not local_path:
            return "Error: local_path cannot be empty for an upload."

        full_local: str = os.path.join(self._workspace_dir, local_path)
        if not self._within_workspace(full_path=full_local):
            return "Error: local_path must be within the workspace directory."
        if not os.path.isfile(path=full_local):
            return f"Error: local file not found: {local_path}"

        confirm_msg: str = (
            f"⚠️ Memtrix wants to upload a file to '{alias}':\n\n"
            f"  Local:  {local_path}\n"
            f"  Remote: {remote_path}\n\n"
            "Allow this transfer? (yes/no)"
        )
        if not confirm_with_user(kwargs, message=confirm_msg):
            return "Transfer denied by user."

        try:
            size, target = manager.scp(
                alias=alias,
                direction="upload",
                local_path=os.path.realpath(full_local),
                remote_path=remote_path,
                max_bytes=MAX_SCP_BYTES,
            )
        except SSHError as exc:
            return f"Error: {exc}"

        return f"Uploaded {local_path} → {alias}:{target} ({_human_size(size)})."

    def _download(self, manager: SSHManager, alias: str, local_path: str, remote_path: str, kwargs: dict[str, Any]) -> str:
        if not local_path:
            basename: str = os.path.basename(remote_path.rstrip("/"))
            if not basename:
                return "Error: could not determine a filename from remote_path; provide local_path."
            local_path = os.path.join(DOWNLOADS_DIR, basename)

        full_local: str = os.path.join(self._workspace_dir, local_path)
        if not self._within_workspace(full_path=full_local):
            return "Error: local_path must be within the workspace directory."
        if os.path.isdir(s=full_local):
            return "Error: local_path is a directory; specify a destination filename."
        if os.path.exists(path=full_local):
            return f"Error: local file already exists: {local_path}. Choose a different local_path."

        rel_local: str = os.path.relpath(full_local, self._workspace_dir)
        confirm_msg: str = (
            f"⚠️ Memtrix wants to download a file from '{alias}':\n\n"
            f"  Remote: {remote_path}\n"
            f"  Local:  {rel_local}\n\n"
            "Allow this transfer? (yes/no)"
        )
        if not confirm_with_user(kwargs, message=confirm_msg):
            return "Transfer denied by user."

        # The parent directory is inside the workspace (verified above); create it.
        os.makedirs(name=os.path.dirname(full_local) or self._workspace_dir, exist_ok=True)

        try:
            size, _ = manager.scp(
                alias=alias,
                direction="download",
                local_path=full_local,
                remote_path=remote_path,
                max_bytes=MAX_SCP_BYTES,
            )
        except SSHError as exc:
            return f"Error: {exc}"

        return f"Downloaded {alias}:{remote_path} → {rel_local} ({_human_size(size)})."

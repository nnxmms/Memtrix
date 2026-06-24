#!/usr/bin/python3

import os
import subprocess
from typing import Any

from src.integrations.git import (
    build_git_env,
    git_ssh_host,
    is_blocked_git_host,
    is_https_git_url,
    is_ssh_git_url,
)
from src.tools.base import BaseTool
from src.tools.utils import validate_url_not_internal

# Directories that must not be used as clone targets
BLOCKED_DIRS: set[str] = {"attachments"}


class GitCloneTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the GitCloneTool which clones a git repository into the workspace.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="git_clone",
            description=(
                "Clone a git repository (GitHub, GitLab, etc.) into the workspace. Both HTTPS "
                "URLs (e.g. https://github.com/user/repo.git) and SSH URLs (e.g. "
                "git@github.com:user/repo.git or ssh://git@host/user/repo.git) are supported. "
                "SSH uses the agent's own key — add its public key (ssh_get_pub_key) to the host "
                "first; private HTTPS repos use the GIT_TOKEN secret."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The repository URL: HTTPS (https://github.com/user/repo.git) or SSH (git@github.com:user/repo.git).",
                    },
                    "directory": {
                        "type": "string",
                        "description": "Optional target directory name relative to the workspace. Defaults to the repository name."
                    }
                },
                "required": ["url"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function clones a git repository into the workspace.
        """
        url: str = kwargs.get("url", "").strip()
        directory: str = kwargs.get("directory", "").strip()

        if not url:
            return "Error: url cannot be empty."

        # Validate the URL: HTTPS is checked for SSRF; SSH is checked against the
        # internal-service blocklist (private LAN hosts are allowed for self-hosting).
        is_ssh: bool = is_ssh_git_url(url)
        if is_ssh:
            host: str | None = git_ssh_host(url)
            if not host or is_blocked_git_host(host):
                return "Error: that SSH host is not allowed."
        elif is_https_git_url(url):
            ssrf_error: str | None = validate_url_not_internal(url)
            if ssrf_error:
                return ssrf_error
        else:
            return ("Error: invalid repository URL. Use an HTTPS URL "
                    "(https://github.com/user/repo.git) or an SSH URL "
                    "(git@github.com:user/repo.git).")

        # Build target path
        if directory:
            target: str = os.path.join(self._workspace_dir, directory)
        else:
            # Derive directory name from the URL (handles https://, ssh:// and scp form).
            repo_name: str = url.rstrip("/").replace(":", "/").rsplit(sep="/", maxsplit=1)[-1]
            if repo_name.endswith(".git"):
                repo_name = repo_name[:-4]
            target = os.path.join(self._workspace_dir, repo_name)

        # Prevent path traversal
        if not os.path.realpath(target).startswith(os.path.realpath(self._workspace_dir)):
            return "Error: path must be within the workspace directory."

        # Block protected directories
        relpath: str = os.path.relpath(target, self._workspace_dir)
        top_level: str = relpath.split(os.sep)[0]
        if top_level in BLOCKED_DIRS:
            return f"Error: '{top_level}/' is a protected directory."

        if os.path.exists(path=target):
            return f"Error: '{relpath}' already exists in the workspace."

        try:
            result: subprocess.CompletedProcess[str] = subprocess.run(
                ["git", "clone", "--depth", "1", url, target],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=self._workspace_dir,
                shell=False,
                env=build_git_env(),
            )
        except subprocess.TimeoutExpired:
            return "Error: clone timed out after 120 seconds."

        if result.returncode != 0:
            error: str = result.stderr.strip()
            if is_ssh and ("permission denied" in error.lower() or "publickey" in error.lower()):
                return ("Error cloning repository: SSH authentication failed. Make sure the "
                        "agent's public key (ssh_get_pub_key) is added to the host's deploy "
                        f"keys or your account.\n\n{error}")
            return f"Error cloning repository: {error}"

        return f"Cloned {url} into {relpath}/"


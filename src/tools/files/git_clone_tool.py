#!/usr/bin/python3

import os
import re
import subprocess
from typing import Any

from src.tools.base import BaseTool
from src.tools.utils import validate_url_not_internal

# Directories that must not be used as clone targets
BLOCKED_DIRS: set[str] = {"memory", "attachments"}

# Only allow URLs that look like legitimate git remotes
_URL_PATTERN: re.Pattern[str] = re.compile(
    r"^https?://[a-zA-Z0-9._\-]+(?::[0-9]+)?/[a-zA-Z0-9._\-/]+(?:\.git)?$"
)


class GitCloneTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the GitCloneTool which clones a git repository into the workspace.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="git_clone",
            description="Clone a public git repository (GitHub, GitLab, etc.) into the workspace. Only HTTPS URLs are supported.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The HTTPS URL of the repository, e.g. https://github.com/user/repo.git"
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

        # Validate URL format to prevent command injection
        if not _URL_PATTERN.match(url):
            return "Error: invalid repository URL. Only HTTPS URLs are supported (e.g. https://github.com/user/repo.git)."

        # Block internal/private network addresses (SSRF protection)
        ssrf_error: str | None = validate_url_not_internal(url)
        if ssrf_error:
            return ssrf_error

        # Build target path
        if directory:
            target: str = os.path.join(self._workspace_dir, directory)
        else:
            # Derive directory name from the URL
            repo_name: str = url.rstrip("/").rsplit(sep="/", maxsplit=1)[-1]
            if repo_name.endswith(".git"):
                repo_name: str = repo_name[:-4]
            target: str = os.path.join(self._workspace_dir, repo_name)

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
            )
        except subprocess.TimeoutExpired:
            return "Error: clone timed out after 120 seconds."

        if result.returncode != 0:
            error: str = result.stderr.strip()
            return f"Error cloning repository: {error}"

        return f"Cloned {url} into {relpath}/"

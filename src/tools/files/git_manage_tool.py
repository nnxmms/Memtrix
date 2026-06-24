#!/usr/bin/python3

import os
import re
import subprocess
from typing import Any
from urllib.parse import urlparse, urlunparse

from src.core.config import CONFIG_PATH
from src.tools.base import BaseTool
from src.tools.utils import confirm_with_user

# Secret env vars (set via the web panel / .env, prefixed MEMTRIX_SECRET_) used to
# authenticate HTTPS pushes without persisting credentials in the repository.
GIT_TOKEN_ENV: str = "MEMTRIX_SECRET_GIT_TOKEN"
GIT_USERNAME_ENV: str = "MEMTRIX_SECRET_GIT_USERNAME"

# Control characters are rejected in config values to prevent gitconfig injection.
_CONTROL_CHARS: re.Pattern[str] = re.compile(r"[\x00-\x1f\x7f]")

# How long any single git invocation may run before it is aborted.
_GIT_TIMEOUT: int = 120


class GitManageTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the GitManageTool which configures the git identity and performs
        commits and pushes on repositories inside the workspace.
        """
        self._workspace_dir: str = workspace_dir
        # The root filesystem is read-only, so the global git config is redirected to
        # the writable data volume. This makes `git config --global` persist across
        # restarts and apply to every repository in the workspace.
        self._data_dir: str = os.path.dirname(CONFIG_PATH)
        self._gitconfig_path: str = os.path.join(self._data_dir, ".gitconfig")
        super().__init__(
            name="git_manage",
            description=(
                "Configure the git identity and run git commits and pushes on repositories "
                "in your workspace. Actions: 'config' sets the commit author name and email "
                "(persisted globally for all repos); 'status' shows the working-tree changes; "
                "'commit' stages and commits changes; 'push' uploads commits to the remote "
                "(asks the user to confirm first). Operates on the workspace root by default, "
                "or a subdirectory via 'directory'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["config", "status", "commit", "push"],
                        "description": "The git operation to perform.",
                    },
                    "directory": {
                        "type": "string",
                        "description": "Optional repository path relative to the workspace. Defaults to the workspace root.",
                    },
                    "name": {
                        "type": "string",
                        "description": "For action 'config': the commit author name (e.g. 'Memtrix').",
                    },
                    "email": {
                        "type": "string",
                        "description": "For action 'config': the commit author email (e.g. 'memtrix@example.com').",
                    },
                    "message": {
                        "type": "string",
                        "description": "For action 'commit': the commit message.",
                    },
                    "add_all": {
                        "type": "boolean",
                        "description": "For action 'commit': stage all changes before committing. Defaults to true.",
                    },
                    "remote": {
                        "type": "string",
                        "description": "For action 'push': the remote name. Defaults to 'origin'.",
                    },
                    "branch": {
                        "type": "string",
                        "description": "For action 'push': the branch to push. Defaults to the current branch.",
                    },
                },
                "required": ["action"],
            },
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function dispatches a git action to its handler.
        """
        action: str = (kwargs.get("action") or "").strip().lower()

        repo_dir, repo_error = self._resolve_repo_dir(kwargs.get("directory", ""))
        if repo_error:
            return repo_error

        if action == "config":
            return self._config(repo_dir=repo_dir, kwargs=kwargs)
        if action == "status":
            return self._status(repo_dir=repo_dir)
        if action == "commit":
            return self._commit(repo_dir=repo_dir, kwargs=kwargs)
        if action == "push":
            return self._push(repo_dir=repo_dir, kwargs=kwargs)
        return f"Error: unknown action '{action}'. Use config, status, commit, or push."

    # --------------------------------------------------------------- path handling

    def _resolve_repo_dir(self, directory: str) -> tuple[str, str | None]:
        """
        This function resolves the target repository directory within the workspace,
        guarding against path traversal. Returns (path, error).
        """
        directory = (directory or "").strip()
        target: str = os.path.join(self._workspace_dir, directory) if directory else self._workspace_dir
        real_target: str = os.path.realpath(target)
        if not (real_target == os.path.realpath(self._workspace_dir)
                or real_target.startswith(os.path.realpath(self._workspace_dir) + os.sep)):
            return ("", "Error: directory must be within the workspace.")
        if not os.path.isdir(real_target):
            return ("", f"Error: '{directory}' is not a directory in the workspace.")
        return (real_target, None)

    # ------------------------------------------------------------------ git runner

    def _run_git(self, args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
        """
        This function runs a git command with a controlled environment: the global
        config is redirected to the writable data volume, the system config is
        ignored, and interactive credential prompts are disabled so a missing
        credential fails fast instead of hanging.
        """
        env: dict[str, str] = {
            **os.environ,
            "HOME": self._data_dir,
            "GIT_CONFIG_GLOBAL": self._gitconfig_path,
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "/bin/echo",
        }
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            cwd=cwd,
            shell=False,
            env=env,
        )

    def _is_git_repo(self, repo_dir: str) -> bool:
        """
        This function returns True when repo_dir is inside a git working tree.
        """
        try:
            result: subprocess.CompletedProcess[str] = self._run_git(
                ["rev-parse", "--is-inside-work-tree"], cwd=repo_dir
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0 and result.stdout.strip() == "true"

    # --------------------------------------------------------------------- actions

    def _config(self, repo_dir: str, kwargs: dict[str, Any]) -> str:
        """
        This function sets the git author name and/or email in the persisted global
        config so every repository in the workspace uses the same identity.
        """
        name: str = (kwargs.get("name") or "").strip()
        email: str = (kwargs.get("email") or "").strip()
        if not name and not email:
            return "Error: provide a name and/or email to configure."
        if name and _CONTROL_CHARS.search(name):
            return "Error: name contains invalid control characters."
        if email and _CONTROL_CHARS.search(email):
            return "Error: email contains invalid control characters."
        if email and ("@" not in email or " " in email):
            return "Error: email does not look valid."

        applied: list[str] = []
        try:
            if name:
                result: subprocess.CompletedProcess[str] = self._run_git(
                    ["config", "--global", "user.name", name], cwd=repo_dir
                )
                if result.returncode != 0:
                    return f"Error setting name: {result.stderr.strip()}"
                applied.append(f"name '{name}'")
            if email:
                result = self._run_git(["config", "--global", "user.email", email], cwd=repo_dir)
                if result.returncode != 0:
                    return f"Error setting email: {result.stderr.strip()}"
                applied.append(f"email '{email}'")
        except subprocess.TimeoutExpired:
            return "Error: git config timed out."

        return f"Configured git {' and '.join(applied)}."

    def _status(self, repo_dir: str) -> str:
        """
        This function reports the working-tree status of a repository.
        """
        if not self._is_git_repo(repo_dir):
            return "Error: not a git repository. Clone one with git_clone first."
        try:
            result: subprocess.CompletedProcess[str] = self._run_git(
                ["status", "--short", "--branch"], cwd=repo_dir
            )
        except subprocess.TimeoutExpired:
            return "Error: git status timed out."
        if result.returncode != 0:
            return f"Error: {result.stderr.strip()}"
        body: str = result.stdout.strip()
        return body or "Working tree clean."

    def _commit(self, repo_dir: str, kwargs: dict[str, Any]) -> str:
        """
        This function stages changes (unless add_all is false) and commits them.
        """
        if not self._is_git_repo(repo_dir):
            return "Error: not a git repository. Clone one with git_clone first."

        message: str = (kwargs.get("message") or "").strip()
        if not message:
            return "Error: a commit message is required."

        add_all: bool = kwargs.get("add_all", True)
        if not isinstance(add_all, bool):
            add_all = True

        try:
            if add_all:
                add_result: subprocess.CompletedProcess[str] = self._run_git(["add", "-A"], cwd=repo_dir)
                if add_result.returncode != 0:
                    return f"Error staging changes: {add_result.stderr.strip()}"

            commit_result: subprocess.CompletedProcess[str] = self._run_git(
                ["commit", "-m", message], cwd=repo_dir
            )
        except subprocess.TimeoutExpired:
            return "Error: git commit timed out."

        if commit_result.returncode != 0:
            combined: str = (commit_result.stdout + "\n" + commit_result.stderr).strip()
            if "nothing to commit" in combined.lower():
                return "Nothing to commit — the working tree is clean."
            if "please tell me who you are" in combined.lower() or "user.email" in combined.lower():
                return ("Error: git identity is not configured. Run git_manage with "
                        "action 'config' to set a name and email first.")
            return f"Error committing: {combined}"

        # Surface the new commit's short hash and subject.
        try:
            head: subprocess.CompletedProcess[str] = self._run_git(
                ["log", "-1", "--pretty=%h %s"], cwd=repo_dir
            )
            summary: str = head.stdout.strip() if head.returncode == 0 else message
        except subprocess.TimeoutExpired:
            summary = message
        return f"Committed: {summary}"

    def _push(self, repo_dir: str, kwargs: dict[str, Any]) -> str:
        """
        This function pushes commits to a remote after the user confirms. HTTPS pushes
        are authenticated with a token from the secrets store when one is available,
        without writing the credential into the repository.
        """
        if not self._is_git_repo(repo_dir):
            return "Error: not a git repository. Clone one with git_clone first."

        remote: str = (kwargs.get("remote") or "origin").strip() or "origin"
        branch: str = (kwargs.get("branch") or "").strip()
        if not branch:
            branch, branch_error = self._current_branch(repo_dir=repo_dir)
            if branch_error:
                return branch_error

        confirm_msg: str = (
            f"⚠️ Push branch '{branch}' to remote '{remote}'?\n\n"
            "This publishes your commits to the remote repository. (yes/no)"
        )
        if not confirm_with_user(kwargs, message=confirm_msg):
            return "Push cancelled by user."

        push_args: list[str] = ["push", remote, branch]
        token: str = os.environ.get(GIT_TOKEN_ENV, "").strip()
        authed_url: str | None = None
        if token:
            authed_url = self._authenticated_remote_url(repo_dir=repo_dir, remote=remote, token=token)
            if authed_url:
                # Push to the explicit authenticated URL instead of the named remote
                # so the credential is never written to the repo's config.
                push_args = ["push", authed_url, branch]

        try:
            result: subprocess.CompletedProcess[str] = self._run_git(push_args, cwd=repo_dir)
        except subprocess.TimeoutExpired:
            return "Error: git push timed out."

        output: str = (result.stdout + "\n" + result.stderr).strip()
        if token:
            output = output.replace(token, "***")

        if result.returncode != 0:
            if "could not read username" in output.lower() or "authentication failed" in output.lower():
                return ("Error: push authentication failed. Set a GIT_TOKEN secret (and "
                        "optionally GIT_USERNAME) in the control panel for HTTPS pushes, or "
                        f"check the remote permissions.\n\n{output}")
            return f"Error pushing: {output}"
        return f"Pushed '{branch}' to '{remote}'.\n\n{output}" if output else f"Pushed '{branch}' to '{remote}'."

    # --------------------------------------------------------------------- helpers

    def _current_branch(self, repo_dir: str) -> tuple[str, str | None]:
        """
        This function returns the current branch name, or an error when the repository
        has no branch checked out (e.g. a detached HEAD).
        """
        try:
            result: subprocess.CompletedProcess[str] = self._run_git(
                ["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir
            )
        except subprocess.TimeoutExpired:
            return ("", "Error: could not determine the current branch (timed out).")
        branch: str = result.stdout.strip()
        if result.returncode != 0 or not branch or branch == "HEAD":
            return ("", "Error: could not determine the current branch. Specify 'branch' explicitly.")
        return (branch, None)

    def _authenticated_remote_url(self, repo_dir: str, remote: str, token: str) -> str | None:
        """
        This function builds an authenticated HTTPS URL for the remote by injecting the
        token (and optional username) from the secrets store. Returns None when the
        remote is missing or is not an HTTPS URL (so SSH remotes are pushed as-is).
        """
        try:
            result: subprocess.CompletedProcess[str] = self._run_git(
                ["remote", "get-url", remote], cwd=repo_dir
            )
        except subprocess.TimeoutExpired:
            return None
        if result.returncode != 0:
            return None

        url: str = result.stdout.strip()
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            return None

        username: str = os.environ.get(GIT_USERNAME_ENV, "").strip()
        credential: str = f"{username}:{token}" if username else token
        netloc: str = f"{credential}@{parsed.hostname}"
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))

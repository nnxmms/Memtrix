#!/usr/bin/python3

import os
import shlex
import subprocess
from typing import Any

from src.integrations.git import (
    build_git_auth_env,
    build_git_env,
    git_ssh_host,
    https_host,
    is_blocked_git_host,
    is_ssh_git_url,
)
from src.tools.base import BaseTool
from src.tools.utils import confirm_with_user

# How long any single git invocation may run before it is aborted.
_GIT_TIMEOUT: int = 180

# Maximum characters of command output returned to the model.
_MAX_OUTPUT: int = 20000

# git subcommands that talk to a remote — these get HTTPS credentials injected and
# have their remote hosts validated against the internal-service blocklist.
_NETWORK_SUBCMDS: frozenset[str] = frozenset({
    "clone", "fetch", "pull", "push", "ls-remote", "remote", "submodule",
})

# git subcommands that publish to a remote and therefore require user confirmation.
_PUBLISH_SUBCMDS: frozenset[str] = frozenset({"push"})

# Directories that must not be used as a working directory or clone target.
_BLOCKED_DIRS: set[str] = {"attachments"}


class GitTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the GitTool — a single tool that runs any git command inside the
        workspace (status, add, branch, commit, rebase, clone, pull, push, …). It
        wires up the agent's SSH identity and, for private HTTPS remotes, an optional
        token, redirects git's global config to the writable data volume, and never
        opens an interactive editor, pager, or credential prompt so commands can't hang.
        """
        self._workspace_dir: str = workspace_dir
        self._token: str = ""
        self._username: str = ""
        super().__init__(
            name="git",
            description=(
                "Run a git command in your workspace. Provide the command exactly as you would "
                "on the shell, without the leading 'git' (e.g. 'status', 'checkout -b feature', "
                "'add -A', 'commit -m \"message\"', 'rebase main', 'clone git@github.com:u/r.git', "
                "'pull', 'push'). Set the git identity once with 'config user.name \"…\"' and "
                "'config user.email \"…\"' before committing. Both HTTPS and SSH remotes work: SSH "
                "uses the agent's key (add it to the host with ssh_get_pub_key), private HTTPS uses "
                "the GIT_TOKEN secret. Commands that publish to a remote (push) ask the user to "
                "confirm first. Operates on the workspace root by default, or a subdirectory via "
                "'directory'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The git command and its arguments, without the leading 'git'. E.g. 'commit -m \"fix bug\"'.",
                    },
                    "directory": {
                        "type": "string",
                        "description": "Optional repository path relative to the workspace. Defaults to the workspace root.",
                    },
                },
                "required": ["command"],
            },
        )

    def set_git_credentials(self, token: str, username: str) -> None:
        """
        This function injects the resolved HTTPS credentials (called at startup). The
        token comes from the GIT_TOKEN secret; an empty token simply disables HTTPS
        authentication (public repos and SSH remotes still work).
        """
        self._token = (token or "").strip()
        self._username = (username or "").strip()

    def execute(self, **kwargs: Any) -> str:
        """
        This function parses and runs a single git command in the workspace.
        """
        command: str = str(kwargs.get("command") or "").strip()
        if not command:
            return "Error: a git command is required."

        # Parse the command safely (no shell). Strip an optional leading 'git'.
        try:
            args: list[str] = shlex.split(command)
        except ValueError as exc:
            return f"Error: could not parse the command: {exc}"
        if args and args[0].lower() == "git":
            args = args[1:]
        if not args:
            return "Error: a git command is required."

        repo_dir, repo_error = self._resolve_repo_dir(str(kwargs.get("directory") or ""))
        if repo_error:
            return repo_error

        # Identify the subcommand (the first token that is not a top-level -c/--flag).
        subcommand: str = self._subcommand(args)

        # Block remotes that target internal Memtrix services (SSRF protection).
        host_error: str | None = self._validate_remote_hosts(args)
        if host_error:
            return host_error

        # Publishing commands (push) require explicit user confirmation.
        if subcommand in _PUBLISH_SUBCMDS:
            confirm_msg: str = (
                f"⚠️ Run `git {' '.join(args)}`?\n\n"
                "This publishes to a remote repository. (yes/no)"
            )
            if not confirm_with_user(kwargs, message=confirm_msg):
                return "Cancelled by user."

        # Build the environment — inject scoped HTTPS credentials for network ops.
        if subcommand in _NETWORK_SUBCMDS and self._token:
            hosts: list[str] = self._credential_hosts(args=args, repo_dir=repo_dir, subcommand=subcommand)
            env: dict[str, str] = build_git_auth_env(token=self._token, username=self._username, hosts=hosts)
        else:
            env = build_git_env()

        try:
            result: subprocess.CompletedProcess[str] = subprocess.run(
                ["git", *args],
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT,
                cwd=repo_dir,
                shell=False,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return f"Error: git command timed out after {_GIT_TIMEOUT} seconds."
        except FileNotFoundError:
            return "Error: git is not installed."

        output: str = (result.stdout + ("\n" + result.stderr if result.stderr else "")).strip()
        if self._token:
            output = output.replace(self._token, "***")
        if len(output) > _MAX_OUTPUT:
            output = output[:_MAX_OUTPUT] + "\n…[output truncated]"

        if result.returncode != 0:
            hint: str = self._failure_hint(subcommand=subcommand, output=output)
            return f"Error (git exited {result.returncode}):\n{output}{hint}" if output else \
                f"Error: git exited with code {result.returncode}.{hint}"
        return output or "Done (no output)."

    # --------------------------------------------------------------------- helpers

    def _resolve_repo_dir(self, directory: str) -> tuple[str, str | None]:
        """
        This function resolves the working directory within the workspace, guarding
        against path traversal. Returns (path, error).
        """
        directory = (directory or "").strip()
        target: str = os.path.join(self._workspace_dir, directory) if directory else self._workspace_dir
        real_target: str = os.path.realpath(target)
        real_root: str = os.path.realpath(self._workspace_dir)
        if not (real_target == real_root or real_target.startswith(real_root + os.sep)):
            return ("", "Error: directory must be within the workspace.")
        relpath: str = os.path.relpath(real_target, real_root)
        if relpath != "." and relpath.split(os.sep)[0] in _BLOCKED_DIRS:
            return ("", "Error: that directory is protected.")
        if not os.path.isdir(real_target):
            return ("", f"Error: '{directory}' is not a directory in the workspace.")
        return (real_target, None)

    def _subcommand(self, args: list[str]) -> str:
        """
        This function returns the git subcommand, skipping any leading top-level
        options (e.g. `-c key=val`, `-C path`) that precede it.
        """
        i: int = 0
        while i < len(args):
            tok: str = args[i]
            if tok in ("-c", "-C", "--git-dir", "--work-tree", "--namespace"):
                i += 2
                continue
            if tok.startswith("-"):
                i += 1
                continue
            return tok.lower()
        return ""

    def _validate_remote_hosts(self, args: list[str]) -> str | None:
        """
        This function rejects any URL argument that targets an internal Memtrix
        service or the loopback interface. Returns an error string or None.
        """
        for tok in args:
            host: str | None = None
            if is_ssh_git_url(tok):
                host = git_ssh_host(tok)
            else:
                host = https_host(tok)
            if host and is_blocked_git_host(host):
                return f"Error: the host '{host}' is not allowed."
        return None

    def _credential_hosts(self, args: list[str], repo_dir: str, subcommand: str) -> list[str]:
        """
        This function determines which HTTPS hosts the token should be scoped to: any
        HTTPS URL present in the command, plus the hosts of the repository's configured
        remotes for network operations that rely on them (push/pull/fetch/ls-remote).
        """
        hosts: set[str] = set()
        for tok in args:
            host: str | None = https_host(tok)
            if host:
                hosts.add(host)

        if not hosts and subcommand in {"push", "pull", "fetch", "ls-remote"}:
            try:
                remotes: subprocess.CompletedProcess[str] = subprocess.run(
                    ["git", "remote", "-v"],
                    capture_output=True, text=True, timeout=15,
                    cwd=repo_dir, shell=False, env=build_git_env(),
                )
                if remotes.returncode == 0:
                    for line in remotes.stdout.splitlines():
                        parts: list[str] = line.split()
                        if len(parts) >= 2:
                            host = https_host(parts[1])
                            if host:
                                hosts.add(host)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
        return sorted(hosts)

    def _failure_hint(self, subcommand: str, output: str) -> str:
        """
        This function appends a short, actionable hint for common git failures.
        """
        low: str = output.lower()
        if "please tell me who you are" in low or ("user.email" in low and "config" in low):
            return ("\n\nHint: set the identity first with git "
                    "'config user.name \"Name\"' and 'config user.email \"you@example.com\"'.")
        if subcommand in _NETWORK_SUBCMDS and ("permission denied" in low or "publickey" in low):
            return ("\n\nHint: SSH authentication failed — add the agent's public key "
                    "(ssh_get_pub_key) to the host's deploy keys or your account.")
        if subcommand in _NETWORK_SUBCMDS and ("authentication failed" in low or "could not read username" in low):
            return ("\n\nHint: HTTPS authentication failed — set a GIT_TOKEN secret "
                    "(and optionally a git username) in the control panel.")
        return ""

#!/usr/bin/python3

import base64
import os
import re
import shlex

from src.core.config import CONFIG_PATH

# The root filesystem is read-only, so git state (global config, SSH identity and
# known_hosts) lives on the writable data volume and persists across restarts.
GIT_DATA_DIR: str = os.path.dirname(CONFIG_PATH)
GIT_CONFIG_GLOBAL: str = os.path.join(GIT_DATA_DIR, ".gitconfig")

# The SSH identity is shared with the SSH sysadmin feature: the public key the user
# registers with GitHub/GitLab (via ssh_get_pub_key) is the same key git uses here.
GIT_SSH_DIR: str = os.path.join(GIT_DATA_DIR, "ssh")
GIT_SSH_KEY: str = os.path.join(GIT_SSH_DIR, "id_ed25519")
GIT_KNOWN_HOSTS: str = os.path.join(GIT_SSH_DIR, "known_hosts")

# Internal service names that must never be reachable as a git remote.
_BLOCKED_GIT_HOSTS: set[str] = {
    "conduit", "chroma", "searxng", "memtrix", "localhost", "host.docker.internal",
}

# scp-like SSH remote, e.g. git@github.com:owner/repo.git
_SSH_SCP_PATTERN: re.Pattern[str] = re.compile(
    r"^[A-Za-z0-9._\-]+@([A-Za-z0-9._\-]+):[A-Za-z0-9][A-Za-z0-9._\-/~]*$"
)
# ssh:// URL, e.g. ssh://git@github.com:22/owner/repo.git
_SSH_URL_PATTERN: re.Pattern[str] = re.compile(
    r"^ssh://(?:[A-Za-z0-9._\-]+@)?([A-Za-z0-9._\-]+)(?::[0-9]+)?/[A-Za-z0-9][A-Za-z0-9._\-/~]*$"
)
# HTTPS remote, e.g. https://github.com/owner/repo.git
_HTTPS_PATTERN: re.Pattern[str] = re.compile(
    r"^https?://[a-zA-Z0-9._\-]+(?::[0-9]+)?/[a-zA-Z0-9._\-/]+(?:\.git)?$"
)


def is_ssh_git_url(url: str) -> bool:
    """
    This function returns True when the URL is an SSH git remote (scp-like or ssh://).
    """
    return bool(_SSH_SCP_PATTERN.match(url) or _SSH_URL_PATTERN.match(url))


def is_https_git_url(url: str) -> bool:
    """
    This function returns True when the URL is an HTTP(S) git remote.
    """
    return bool(_HTTPS_PATTERN.match(url))


def https_host(url: str) -> str | None:
    """
    This function returns the hostname of an HTTP(S) URL, or None when the value is
    not an HTTP(S) URL.
    """
    if "://" not in url:
        return None
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https") and parsed.hostname:
        return parsed.hostname
    return None


def git_ssh_host(url: str) -> str | None:
    """
    This function extracts the hostname from an SSH git URL, or None when the URL is
    not a recognised SSH remote.
    """
    match: re.Match[str] | None = _SSH_SCP_PATTERN.match(url) or _SSH_URL_PATTERN.match(url)
    return match.group(1) if match else None


def is_blocked_git_host(host: str) -> bool:
    """
    This function returns True when an SSH git host targets an internal Memtrix
    service or the loopback interface, which must never be used as a remote.
    """
    return host.strip().lower() in _BLOCKED_GIT_HOSTS


def build_git_ssh_command() -> str:
    """
    This function builds the GIT_SSH_COMMAND used for git-over-SSH. It pins the agent's
    identity (when present), records host keys on first use in a persisted known_hosts
    file, and disables interactive prompts so a missing/declined key fails fast.
    """
    parts: list[str] = [
        "ssh",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", shlex.quote(f"UserKnownHostsFile={GIT_KNOWN_HOSTS}"),
        "-o", "ConnectTimeout=20",
        "-o", "BatchMode=yes",
    ]
    if os.path.exists(path=GIT_SSH_KEY):
        # Offer only our own identity so agent forwarding / other keys are not tried.
        parts += ["-i", shlex.quote(GIT_SSH_KEY), "-o", "IdentitiesOnly=yes"]
    return " ".join(parts)


def build_git_env() -> dict[str, str]:
    """
    This function returns the environment for every git invocation: the global config
    is redirected to the writable data volume, the system config is ignored,
    interactive credential prompts are disabled, and git-over-SSH is configured to use
    the agent's key and a persisted known_hosts file.
    """
    os.makedirs(name=GIT_SSH_DIR, mode=0o700, exist_ok=True)
    return {
        **os.environ,
        "HOME": GIT_DATA_DIR,
        "GIT_CONFIG_GLOBAL": GIT_CONFIG_GLOBAL,
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/echo",
        "GIT_SSH_COMMAND": build_git_ssh_command(),
        # Never open an interactive editor or pager — captured output has no TTY, but
        # be explicit so commands like `rebase -i`, `merge`, or `commit` can't hang.
        "GIT_EDITOR": "true",
        "GIT_SEQUENCE_EDITOR": "true",
        "GIT_PAGER": "cat",
    }


def build_git_auth_env(token: str, username: str, hosts: list[str]) -> dict[str, str]:
    """
    This function returns the git environment with HTTPS credentials injected as a
    per-host Authorization header for each host in `hosts`. Scoping the credential to
    the specific remote hosts (rather than a global header) prevents the token from
    being sent to any other site. SSH remotes ignore this and use the agent's key.
    Returns the plain environment unchanged when no token or no hosts are supplied.
    """
    env: dict[str, str] = build_git_env()
    token = (token or "").strip()
    if not token or not hosts:
        return env

    # GitHub/GitLab accept Basic auth with any username when a token is the password;
    # default to a conventional placeholder when none is configured.
    credential: str = f"{username.strip() or 'x-access-token'}:{token}"
    header: str = "Authorization: Basic " + base64.b64encode(credential.encode()).decode()

    # Inject one scoped http.<url>.extraheader entry per host via the GIT_CONFIG_*
    # environment protocol, so the secret never touches the repo or global config.
    count: int = 0
    for host in hosts:
        env[f"GIT_CONFIG_KEY_{count}"] = f"http.https://{host}/.extraheader"
        env[f"GIT_CONFIG_VALUE_{count}"] = header
        count += 1
    env["GIT_CONFIG_COUNT"] = str(count)
    return env


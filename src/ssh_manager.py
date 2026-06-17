#!/usr/bin/python3

import base64
import hashlib
import ipaddress
import json
import logging
import os
import re
import select
import socket
import threading
import time
import uuid
from typing import Any, Callable

import paramiko

from src.config import CONFIG_PATH, load_config, resolve_ssh_config

logger: logging.Logger = logging.getLogger(__name__)

# Directory holding the agent's SSH identity, host registry and known_hosts. Lives
# on the writable data volume so it survives restarts of the read-only container.
SSH_DIR: str = os.path.join(os.path.dirname(CONFIG_PATH), "ssh")

# Internal Memtrix service names that must never be reachable over SSH. Unlike the
# HTTP tools, private LAN addresses ARE allowed here — administering hosts on the
# local network is the whole point. Only loopback/link-local and the bot's own
# Docker services are refused.
BLOCKED_SSH_HOSTS: set[str] = {"conduit", "chroma", "searxng", "memtrix", "localhost", "host.docker.internal"}

# Alias must be a short, safe identifier (used as a dict key and in messages).
_ALIAS_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Tool files implementing the SSH sysadmin capability. Gated behind ssh.enabled and
# reserved for the main agent (excluded from sub-agents), mirroring memory tools.
SSH_TOOL_FILES: set[str] = {
    "ssh_gen_key_tool.py",
    "ssh_get_pub_key_tool.py",
    "ssh_add_host_tool.py",
    "ssh_remove_host_tool.py",
    "ssh_get_remote_hosts_tool.py",
    "ssh_connect_tool.py",
    "ssh_run_tool.py",
    "ssh_disconnect_tool.py",
}


class SSHError(Exception):
    """Raised for SSH connection or command failures surfaced to the agent."""


class SSHTimeout(SSHError):
    """Raised when a remote command exceeds the configured timeout."""


def _fingerprint(key: paramiko.PKey) -> str:
    """
    This function returns the OpenSSH-style SHA256 fingerprint of a host key.
    """
    digest: bytes = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


class SSHConnection:

    def __init__(self, client: paramiko.SSHClient, command_timeout: int, max_output: int) -> None:
        """
        This is the SSHConnection class which wraps a persistent interactive shell
        over a single SSH connection. Working directory and environment persist
        between commands, exactly like a human working in a terminal.
        """
        self._client: paramiko.SSHClient = client
        self._chan: paramiko.Channel | None = None
        self._command_timeout: int = command_timeout
        self._max_output: int = max_output
        self._lock: threading.Lock = threading.Lock()

    def open_shell(self) -> None:
        """
        This function opens the interactive shell channel and quiets it down so
        command output can be captured cleanly (no prompt noise, no input echo).
        """
        self._chan = self._client.invoke_shell(width=1024, height=80)
        self._chan.settimeout(0.0)
        # Silence the prompt and disable terminal echo so captured output is clean.
        self._chan.send("export PS1='' PROMPT_COMMAND='' ; stty -echo 2>/dev/null ; export LANG=C LC_ALL=C 2>/dev/null\n")
        # Run a no-op to drain the login banner / motd before the first real command.
        try:
            self.run(command=":")
        except Exception as exc:
            logger.debug("Shell warm-up command failed: %s", exc)

    def is_active(self) -> bool:
        """
        This function reports whether the underlying channel and transport are live.
        """
        transport: paramiko.Transport | None = self._client.get_transport()
        return (
            self._chan is not None
            and not self._chan.closed
            and transport is not None
            and transport.is_active()
        )

    def run(self, command: str, password: str | None = None) -> tuple[str, int]:
        """
        This function runs a single command in the persistent shell and returns its
        output and exit code. A password, when given, is written to the command's
        stdin (used for sudo -S).
        """
        with self._lock:
            return self._run(command=command, password=password)

    def _run(self, command: str, password: str | None) -> tuple[str, int]:
        if self._chan is None or self._chan.closed:
            raise SSHError("The shell session is closed.")

        token: str = uuid.uuid4().hex

        # The quotes split the markers so the line the PTY echoes back ("MTX\"\"B-")
        # never matches the markers the shell actually prints ("MTXB-"). This lets
        # us locate the real command output even if terminal echo is still on.
        line: str = f'echo "MTX""B-{token}"; {command}; echo "MTX""E-{token}-$?"\n'
        self._chan.send(line)
        if password is not None:
            self._chan.send(password + "\n")

        begin: str = f"MTXB-{token}"
        end_re: re.Pattern[str] = re.compile(rf"MTXE-{token}-(-?\d+)")

        buf: str = ""
        deadline: float = time.time() + self._command_timeout
        while True:
            if time.time() > deadline:
                raise SSHTimeout(f"Command timed out after {self._command_timeout}s.")

            select.select([self._chan], [], [], 0.2)
            if self._chan.recv_ready():
                chunk: bytes = self._chan.recv(65536)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", errors="replace")
                match: re.Match[str] | None = end_re.search(buf)
                if match and begin in buf:
                    return self._extract(buf=buf, begin=begin, match=match)
            elif self._chan.closed or self._chan.exit_status_ready():
                break

        raise SSHError("Connection closed before the command completed.")

    def _extract(self, buf: str, begin: str, match: re.Match[str]) -> tuple[str, int]:
        exit_code: int = int(match.group(1))
        start: int = buf.index(begin) + len(begin)
        newline: int = buf.find("\n", start)
        if newline != -1:
            start = newline + 1
        output: str = buf[start:match.start()]
        output = output.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if len(output) > self._max_output:
            output = output[: self._max_output] + "\n... [output truncated]"
        return output, exit_code

    def close(self) -> None:
        """
        This function closes the channel and the underlying connection.
        """
        try:
            if self._chan is not None:
                self._chan.close()
        finally:
            self._client.close()


class SSHManager:

    _instance: "SSHManager | None" = None
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        """
        This is the SSHManager class — a process-wide singleton that owns the
        agent's SSH identity, the registered host list, and any open persistent
        connections. Because tools are long-lived singletons, a connection opened
        by one tool call stays available to later tool calls until disconnected.
        """
        self._ssh_dir: str = SSH_DIR
        self._key_path: str = os.path.join(self._ssh_dir, "id_ed25519")
        self._pub_path: str = os.path.join(self._ssh_dir, "id_ed25519.pub")
        self._known_hosts: str = os.path.join(self._ssh_dir, "known_hosts")
        self._hosts_path: str = os.path.join(self._ssh_dir, "hosts.json")

        # Open connections and RAM-only sudo passwords, keyed by host alias.
        self._connections: dict[str, SSHConnection] = {}
        self._sudo_pw: dict[str, str] = {}
        self._lock: threading.Lock = threading.Lock()

        # Timeouts and limits from config, with safe fallbacks.
        try:
            cfg: dict[str, Any] = resolve_ssh_config(config=load_config())
        except Exception:
            cfg = resolve_ssh_config(config={})
        self._connect_timeout: int = int(cfg["connect_timeout"])
        self._command_timeout: int = int(cfg["command_timeout"])
        self._max_output: int = int(cfg["max_output_chars"])

        os.makedirs(name=self._ssh_dir, mode=0o700, exist_ok=True)

    @classmethod
    def get_instance(cls) -> "SSHManager":
        """
        This function returns the process-wide SSHManager singleton.
        """
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ----- Key management ---------------------------------------------------

    def gen_key(self, force: bool = False) -> str:
        """
        This function generates an ed25519 keypair if one does not already exist
        (or unconditionally when force is set) and returns the public key. The
        private key is written 0600 and is never returned.
        """
        os.makedirs(name=self._ssh_dir, mode=0o700, exist_ok=True)
        if os.path.exists(path=self._key_path) and not force:
            return self.get_pub_key() or ""

        # cryptography ships with paramiko and produces OpenSSH-format keys.
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        key: Ed25519PrivateKey = Ed25519PrivateKey.generate()
        priv_bytes: bytes = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.OpenSSH,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pub_bytes: bytes = key.public_key().public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        )
        pub_str: str = pub_bytes.decode() + " memtrix"

        # Write the private key with a restrictive umask to avoid a readable window.
        old_umask: int = os.umask(0o077)
        try:
            with open(file=self._key_path, mode="wb") as f:
                f.write(priv_bytes)
            os.chmod(path=self._key_path, mode=0o600)
        finally:
            os.umask(old_umask)

        with open(file=self._pub_path, mode="w") as f:
            f.write(pub_str + "\n")
        os.chmod(path=self._pub_path, mode=0o644)

        logger.info("Generated new ed25519 SSH key at %s", self._key_path)
        return pub_str

    def get_pub_key(self) -> str | None:
        """
        This function returns the public key string, or None if no key exists yet.
        """
        if not os.path.exists(path=self._pub_path):
            return None
        with open(file=self._pub_path, mode="r") as f:
            return f.read().strip()

    def _load_pkey(self) -> paramiko.PKey:
        if not os.path.exists(path=self._key_path):
            raise SSHError("No SSH key found. Generate one first with ssh_gen_key.")
        return paramiko.Ed25519Key.from_private_key_file(filename=self._key_path)

    # ----- Host registry ----------------------------------------------------

    def _read_hosts(self) -> dict[str, dict[str, Any]]:
        if not os.path.exists(path=self._hosts_path):
            return {}
        try:
            with open(file=self._hosts_path, mode="r") as f:
                data: Any = json.load(fp=f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_hosts(self, hosts: dict[str, dict[str, Any]]) -> None:
        tmp: str = self._hosts_path + ".tmp"
        with open(file=tmp, mode="w") as f:
            json.dump(obj=hosts, fp=f, indent=2)
        os.replace(src=tmp, dst=self._hosts_path)

    def add_host(self, alias: str, hostname: str, username: str, port: int = 22) -> None:
        """
        This function registers a remote host under a short alias.
        """
        if not _ALIAS_PATTERN.match(alias):
            raise SSHError("Invalid alias. Use letters, digits, '.', '_' or '-' (max 64 chars).")
        if not hostname.strip():
            raise SSHError("hostname cannot be empty.")
        if not username.strip():
            raise SSHError("username cannot be empty.")
        if not (1 <= int(port) <= 65535):
            raise SSHError("port must be between 1 and 65535.")

        with self._lock:
            hosts: dict[str, dict[str, Any]] = self._read_hosts()
            hosts[alias] = {"hostname": hostname.strip(), "port": int(port), "username": username.strip()}
            self._write_hosts(hosts=hosts)

    def remove_host(self, alias: str) -> None:
        """
        This function unregisters a host alias and disconnects it if open.
        """
        self.disconnect(alias=alias)
        with self._lock:
            hosts: dict[str, dict[str, Any]] = self._read_hosts()
            if alias not in hosts:
                raise SSHError(f"No host registered under alias '{alias}'.")
            del hosts[alias]
            self._write_hosts(hosts=hosts)
            self._sudo_pw.pop(alias, None)

    def list_hosts(self) -> list[dict[str, Any]]:
        """
        This function returns the registered hosts annotated with connection status.
        """
        hosts: dict[str, dict[str, Any]] = self._read_hosts()
        result: list[dict[str, Any]] = []
        for alias, info in sorted(hosts.items()):
            conn: SSHConnection | None = self._connections.get(alias)
            connected: bool = conn is not None and conn.is_active()
            result.append({
                "alias": alias,
                "hostname": info.get("hostname", ""),
                "port": info.get("port", 22),
                "username": info.get("username", ""),
                "connected": connected,
            })
        return result

    # ----- Host-key trust (TOFU) -------------------------------------------

    def _guard_target(self, hostname: str) -> None:
        if hostname.lower() in BLOCKED_SSH_HOSTS:
            raise SSHError("Refusing to SSH to an internal Memtrix service.")
        candidates: list[str] = []
        try:
            ipaddress.ip_address(address=hostname)
            candidates.append(hostname)
        except ValueError:
            try:
                for *_, sockaddr in socket.getaddrinfo(host=hostname, port=None):
                    candidates.append(sockaddr[0])
            except socket.gaierror:
                return
        for addr in candidates:
            try:
                ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(address=addr)
            except ValueError:
                continue
            if ip.is_loopback or ip.is_link_local:
                raise SSHError("Refusing to SSH to a loopback or link-local address.")

    def _host_entry(self, hostname: str, port: int) -> str:
        return hostname if port == 22 else f"[{hostname}]:{port}"

    def _host_is_known(self, hostname: str, port: int) -> bool:
        if not os.path.exists(path=self._known_hosts):
            return False
        try:
            host_keys: paramiko.HostKeys = paramiko.HostKeys(filename=self._known_hosts)
        except OSError:
            return False
        return host_keys.lookup(hostname=self._host_entry(hostname=hostname, port=port)) is not None

    def _probe_host_key(self, hostname: str, port: int) -> paramiko.PKey:
        sock: socket.socket = socket.create_connection(address=(hostname, port), timeout=self._connect_timeout)
        transport: paramiko.Transport = paramiko.Transport(sock=sock)
        try:
            transport.start_client(timeout=self._connect_timeout)
            key: paramiko.PKey | None = transport.get_remote_server_key()
            if key is None:
                raise SSHError("Could not retrieve the remote host key.")
            return key
        finally:
            transport.close()

    def _append_known_host(self, hostname: str, port: int, key: paramiko.PKey) -> None:
        entry: str = self._host_entry(hostname=hostname, port=port)
        line: str = f"{entry} {key.get_name()} {key.get_base64()}\n"
        with open(file=self._known_hosts, mode="a") as f:
            f.write(line)
        os.chmod(path=self._known_hosts, mode=0o600)

    # ----- Connection lifecycle --------------------------------------------

    def connect(self, alias: str, confirm_host_key: Callable[[str, str, str], bool]) -> None:
        """
        This function opens a persistent connection to a registered host. On first
        contact the host key is verified trust-on-first-use: confirm_host_key is
        called with (key_type, fingerprint, target) and must return True to trust
        and remember the key.
        """
        with self._lock:
            existing: SSHConnection | None = self._connections.get(alias)
            if existing is not None and existing.is_active():
                return
            hosts: dict[str, dict[str, Any]] = self._read_hosts()
            host: dict[str, Any] | None = hosts.get(alias)
        if host is None:
            raise SSHError(f"No host registered under alias '{alias}'. Add it with ssh_add_host.")

        hostname: str = str(host["hostname"])
        port: int = int(host.get("port", 22))
        username: str = str(host["username"])

        self._guard_target(hostname=hostname)
        pkey: paramiko.PKey = self._load_pkey()

        # Trust-on-first-use host-key verification.
        if not self._host_is_known(hostname=hostname, port=port):
            key: paramiko.PKey = self._probe_host_key(hostname=hostname, port=port)
            target: str = self._host_entry(hostname=hostname, port=port)
            if not confirm_host_key(key.get_name(), _fingerprint(key=key), target):
                raise SSHError("Host key was not trusted; connection aborted.")
            self._append_known_host(hostname=hostname, port=port, key=key)

        client: paramiko.SSHClient = paramiko.SSHClient()
        if os.path.exists(path=self._known_hosts):
            client.load_host_keys(filename=self._known_hosts)
        client.set_missing_host_key_policy(policy=paramiko.RejectPolicy())

        try:
            client.connect(
                hostname=hostname,
                port=port,
                username=username,
                pkey=pkey,
                timeout=self._connect_timeout,
                banner_timeout=self._connect_timeout,
                auth_timeout=self._connect_timeout,
                allow_agent=False,
                look_for_keys=False,
            )
        except paramiko.AuthenticationException:
            raise SSHError(
                f"Authentication failed for {username}@{hostname}. Make sure the public key "
                "(ssh_get_pub_key) is installed in the host's authorized_keys."
            )
        except Exception as exc:
            raise SSHError(f"Could not connect to {username}@{hostname}:{port}: {exc}")

        conn: SSHConnection = SSHConnection(
            client=client,
            command_timeout=self._command_timeout,
            max_output=self._max_output,
        )
        conn.open_shell()
        with self._lock:
            self._connections[alias] = conn
        logger.info("Opened SSH session to %s (%s@%s:%d)", alias, username, hostname, port)

    def is_connected(self, alias: str) -> bool:
        """
        This function reports whether a live session exists for an alias.
        """
        conn: SSHConnection | None = self._connections.get(alias)
        return conn is not None and conn.is_active()

    def run(self, alias: str, command: str, sudo: bool = False,
            ask_password: Callable[[], str] | None = None) -> tuple[str, int]:
        """
        This function runs a command in the open session for an alias. When sudo is
        requested, it first tries non-interactive sudo (-n flag). If that fails due to
        a password requirement, it asks for the password via ask_password and caches
        it in RAM for the alias. This avoids hanging on password prompts when the user
        has passwordless sudo configured.
        """
        conn: SSHConnection | None = self._connections.get(alias)
        if conn is None or not conn.is_active():
            raise SSHError(f"Not connected to '{alias}'. Open a session first with ssh_connect.")

        if sudo:
            # First, try non-interactive sudo (-n flag). This succeeds if the user
            # has passwordless sudo (NOPASSWD) or if a prior call already cached the password.
            # The -k flag flushes any cached credentials so we always get a fresh result.
            test_cmd: str = f"sudo -n -k -p '' -- {command}"
            output, exit_code = conn.run(command=test_cmd, password=None)

            # Check if the non-interactive attempt failed due to password requirement
            lowered: str = output.lower()
            if exit_code != 0 and (
                "a password is required" in lowered
                or "sudo: a password is required" in lowered
                or "[sudo] password" in lowered
                or "is not in the sudoers" in lowered
            ):
                # Password-protected sudo is needed. Check if we have a cached password.
                password: str | None = self._sudo_pw.get(alias)
                if password is None:
                    # No cached password; ask the user for it.
                    if ask_password is None:
                        raise SSHError("A sudo password is required but no prompt is available.")
                    password = ask_password()
                    if not password:
                        raise SSHError("No sudo password was provided; command not run.")
                    self._sudo_pw[alias] = password

                # Retry the command with the password via stdin (-S flag).
                command = f"sudo -k -S -p '' -- {command}"
                output, exit_code = conn.run(command=command, password=password)

                # If the password is wrong, clear the cached entry so we ask again next time.
                if "incorrect password" in lowered or "sorry, try again" in lowered:
                    self._sudo_pw.pop(alias, None)
            elif exit_code == 0:
                # Non-interactive sudo succeeded; return the result.
                return output, exit_code
            else:
                # Some other error occurred; if we already have a cached password, try with it.
                password: str | None = self._sudo_pw.get(alias)
                if password is not None:
                    command = f"sudo -k -S -p '' -- {command}"
                    output, exit_code = conn.run(command=command, password=password)

        else:
            output, exit_code = conn.run(command=command, password=None)

        return output, exit_code


    def disconnect(self, alias: str) -> bool:
        """
        This function closes the session for an alias, if one is open. Returns True
        if a session was closed.
        """
        with self._lock:
            conn: SSHConnection | None = self._connections.pop(alias, None)
            self._sudo_pw.pop(alias, None)
        if conn is None:
            return False
        try:
            conn.close()
        except Exception as exc:
            logger.debug("Error closing SSH session %s: %s", alias, exc)
        logger.info("Closed SSH session to %s", alias)
        return True

    def disconnect_all(self) -> None:
        """
        This function closes every open session. Used on shutdown.
        """
        with self._lock:
            aliases: list[str] = list(self._connections.keys())
        for alias in aliases:
            self.disconnect(alias=alias)

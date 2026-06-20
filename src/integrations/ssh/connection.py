#!/usr/bin/python3

import logging
import re
import select
import threading
import time
import uuid

import paramiko

from src.integrations.ssh.exceptions import SSHError, SSHTimeout

logger: logging.Logger = logging.getLogger(__name__)


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

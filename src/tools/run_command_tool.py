#!/usr/bin/python3

import subprocess
from typing import Any

from src.tools.base import BaseTool
from src.secrets import get_sanitized_env

# Maximum output length to return
MAX_OUTPUT_LENGTH: int = 4000

# Command timeout in seconds
COMMAND_TIMEOUT: int = 30


class RunCommandTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the RunCommandTool which executes shell commands inside the container.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="run_command",
            description="Execute a shell command and return its output. Runs inside the Memtrix container as a non-root user. Use this for file operations, running scripts, or any task that benefits from shell access.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute."
                    }
                },
                "required": ["command"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function executes a shell command and returns the output.
        """
        command: str = kwargs.get("command", "")
        if not command:
            return "Error: command cannot be empty."

        try:
            result: subprocess.CompletedProcess = subprocess.run(
                args=command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT,
                cwd=self._workspace_dir,
                env=get_sanitized_env()
            )
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {COMMAND_TIMEOUT} seconds."

        # Combine stdout and stderr
        output: str = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr

        # Trim if too long
        if len(output) > MAX_OUTPUT_LENGTH:
            output: Any | str = output[:MAX_OUTPUT_LENGTH] + "\n\n[… output truncated]"

        if not output:
            return f"Command completed with exit code {result.returncode}. No output."

        return f"Exit code: {result.returncode}\n\n{output}"

#!/usr/bin/python3

from typing import Any

from src.tools.base import BaseTool


class SpawnWorkerTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the SpawnWorkerTool which launches an ephemeral background worker
        agent to complete a self-contained task without blocking the conversation.
        """
        self._workspace_dir: str = workspace_dir
        self._worker_manager: Any = None
        super().__init__(
            name="spawn_worker",
            description=(
                "Spawn an ephemeral background worker agent to autonomously complete a self-contained task "
                "WITHOUT blocking the conversation. Use this for longer-running, independent work the user "
                "shouldn't have to wait on (e.g. 'research X across several sources and write a summary file', "
                "'clone repo Y and refactor module Z'). You get a worker id back immediately and can keep "
                "talking to the user. When the worker finishes you are automatically notified with its result "
                "and you deliver the outcome to the user — there is no need to poll or check on it. "
                "Write the task as a complete, standalone instruction: the worker has its own fresh context, "
                "no memory, and cannot ask you or the user questions. Workers can use web, file, git and docs "
                "tools but cannot manage agents, memory, SSH, email, skills, or spawn further workers."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "A complete, self-contained instruction describing exactly what the worker should "
                            "accomplish and what its final answer should contain. Include all needed context — "
                            "the worker cannot see this conversation and cannot ask follow-up questions."
                        ),
                    },
                },
                "required": ["task"],
            },
        )

    def set_worker_manager(self, manager: Any) -> None:
        """
        This function sets the worker manager reference.
        """
        self._worker_manager = manager

    def execute(self, **kwargs: Any) -> str:
        """
        This function spawns a background worker for the given task and returns its
        id immediately, leaving the conversation free to continue.
        """
        if not self._worker_manager:
            return "Error: worker support is not available."

        task: str = kwargs.get("task", "").strip()
        if not task:
            return "Error: task cannot be empty."

        # Workers have no Matrix identity; the originating room is needed so the
        # result can be delivered back to the right conversation when it finishes.
        room_id: str = kwargs.get("_room_id", "") or ""
        if not room_id:
            return "Error: workers can only be spawned from a live conversation."

        # No recursion: a worker (depth > 0) must not spawn further workers. Workers
        # don't get this tool, but guard defensively in case of misconfiguration.
        if int(kwargs.get("_agent_depth", 0) or 0) > 0:
            return "Error: workers cannot spawn other workers."

        worker_id: str = self._worker_manager.spawn(task=task, room_id=room_id)
        if not worker_id:
            return (
                "Error: too many workers are already running. Wait for one to finish before spawning another."
            )

        return (
            f"Worker {worker_id} started in the background. You don't need to wait or check on it — "
            "you'll be notified automatically with the result when it finishes. Continue helping the user."
        )

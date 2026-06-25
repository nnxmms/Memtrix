#!/usr/bin/python3

import logging
import queue
import secrets
import threading
from typing import Any, Callable

from src.agents.orchestrator import Orchestrator
from src.core.session import Session

logger: logging.Logger = logging.getLogger(__name__)

# Depth marker passed to a worker's orchestrator so tools that gate on inter-agent
# depth (and the no-recursion rules) treat worker runs as non-interactive.
_WORKER_AGENT_DEPTH: int = 1


class WorkerManager:

    def __init__(self, orchestrator: Orchestrator, sessions_dir: str,
                 trigger: Callable[..., None], max_concurrent: int = 4) -> None:
        """
        This is the WorkerManager which spawns ephemeral background worker agents.

        A worker is a lightweight Orchestrator run on a daemon thread with an
        in-memory session, no Matrix identity and no memory. Spawning returns a
        worker id immediately so the main conversation is never blocked. When a
        worker finishes (success or failure) its result is placed on a shared
        queue; a single WorkerWatcher daemon thread blocks on that queue and fires
        the registered trigger callback so the main agent can deliver the outcome
        to the originating room — no polling, no external event bus.
        """
        # Shared, restricted orchestrator reused for every worker run. Orchestrator.run
        # holds no mutable per-request state, so a single instance serves all workers;
        # each run gets its own ephemeral session and a unique pseudo-room id.
        self._orchestrator: Orchestrator = orchestrator

        # Sessions root (only used to satisfy the Session constructor; ephemeral
        # sessions never write here).
        self._sessions_dir: str = sessions_dir

        # Called on the watcher thread when a worker finishes:
        # trigger(room_id, worker_id, task, result, ok).
        self._trigger: Callable[..., None] = trigger

        # Maximum number of workers running at once (bounds threads and LLM load).
        self._max_concurrent: int = max(1, int(max_concurrent))

        # Completed-worker results awaiting delivery.
        self._results: queue.Queue[tuple[str, str, str, str, bool]] = queue.Queue()

        # Registry of in-flight workers (worker_id -> metadata), guarded by the lock.
        self._active: dict[str, dict[str, Any]] = {}
        self._lock: threading.Lock = threading.Lock()

        # Ensure the watcher is started exactly once.
        self._started: bool = False

    def start(self) -> None:
        """
        This function starts the WorkerWatcher daemon thread that delivers finished
        worker results. Safe to call more than once.
        """
        with self._lock:
            if self._started:
                return
            self._started = True
        watcher: threading.Thread = threading.Thread(
            target=self._watch, name="worker-watcher", daemon=True
        )
        watcher.start()
        logger.info("Worker watcher started (max_concurrent=%d)", self._max_concurrent)

    def active_count(self) -> int:
        """
        This function returns the number of workers currently running.
        """
        with self._lock:
            return len(self._active)

    def spawn(self, task: str, room_id: str) -> str:
        """
        This function spawns a background worker for the given task and returns its
        worker id immediately. The worker runs on its own daemon thread so the
        caller (the main agent) is never blocked. Returns an empty string when the
        concurrency limit is reached.
        """
        with self._lock:
            if len(self._active) >= self._max_concurrent:
                return ""
            worker_id: str = secrets.token_hex(4)
            self._active[worker_id] = {"task": task, "room_id": room_id}

        thread: threading.Thread = threading.Thread(
            target=self._run_worker,
            args=(worker_id, task, room_id),
            name=f"worker-{worker_id}",
            daemon=True,
        )
        thread.start()
        logger.info("Spawned worker %s for room %s", worker_id, room_id)
        return worker_id

    def _run_worker(self, worker_id: str, task: str, room_id: str) -> None:
        """
        This function runs a single worker task to completion on its own thread and
        enqueues the result for the watcher. Any failure is captured and reported as
        a failed result rather than crashing the thread silently.
        """
        result: str
        ok: bool
        try:
            session: Session = Session(sessions_dir=self._sessions_dir, ephemeral=True)
            result = self._orchestrator.run(
                user_message=task,
                session=session,
                room_id=f"worker:{worker_id}",
                agent_depth=_WORKER_AGENT_DEPTH,
            )
            ok = True
            logger.info("Worker %s finished (length=%d)", worker_id, len(result))
        except Exception as e:
            result = str(e)
            ok = False
            logger.error("Worker %s failed: %s", worker_id, e, exc_info=True)

        self._results.put((worker_id, room_id, task, result, ok))

    def _watch(self) -> None:
        """
        This function is the WorkerWatcher loop. It blocks on the result queue and,
        for each finished worker, fires the trigger callback that hands the result
        back to the main agent. The trigger is expected to dispatch any heavy work
        (the main agent run) onto its own thread so one slow room cannot stall the
        delivery of other workers.
        """
        while True:
            worker_id, room_id, task, result, ok = self._results.get()
            try:
                self._trigger(room_id=room_id, worker_id=worker_id, task=task, result=result, ok=ok)
            except Exception as e:
                logger.error("Worker trigger failed for %s: %s", worker_id, e, exc_info=True)
            finally:
                with self._lock:
                    self._active.pop(worker_id, None)

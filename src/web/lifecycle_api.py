#!/usr/bin/python3

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from src import __version__
from src.config import load_config
from src.lifecycle import (
    is_agent_alive,
    is_deriver_paused,
    read_heartbeat,
    request_restart,
    restart_requested,
)
from src.representation import resolve_memory_config
from src.verification import validate_config
from src.web.deps import get_store
from src.web.schemas import MessageResponse, StatusResponse

logger: logging.Logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(prefix="/api", tags=["lifecycle"])


def _memory_count() -> int:
    """
    This function returns the number of stored conclusions, or 0 when memory is
    disabled or unavailable. It avoids loading the embedding model when memory is
    off so the status endpoint stays lightweight.
    """
    try:
        if not resolve_memory_config(config=load_config())["enabled"]:
            return 0
        return get_store().count()
    except Exception as exc:
        logger.debug("Could not read memory count: %s", exc)
        return 0


@router.get("/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    """
    This endpoint reports overall agent and panel health.
    """
    return StatusResponse(
        version=__version__,
        agent_alive=is_agent_alive(),
        heartbeat=read_heartbeat(),
        deriver_paused=is_deriver_paused(),
        restart_requested=restart_requested(),
        memory_count=_memory_count(),
    )


@router.post("/restart", response_model=MessageResponse)
def restart() -> MessageResponse:
    """
    This endpoint validates the current on-disk config and, when valid, requests a
    supervised restart of the agent process. A malformed config is never applied.
    """
    config: dict[str, Any] = load_config()
    errors: list[str] = validate_config(config=config)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"errors": errors},
        )
    request_restart()
    return MessageResponse(message="Restart requested.")


@router.get("/restart/stream")
async def restart_stream() -> StreamingResponse:
    """
    This endpoint streams Server-Sent Events tracking a restart: it waits for the
    agent's heartbeat to go stale (process stopping) and then become fresh again
    (process back up).
    """
    async def _events() -> AsyncIterator[str]:
        yield _sse(phase="stopping", detail="Waiting for agent to stop...")

        # Wait for the heartbeat to go stale (up to ~30s)
        went_down: bool = False
        for _ in range(60):
            if not is_agent_alive():
                went_down = True
                break
            await asyncio.sleep(0.5)

        if not went_down:
            # The supervisor may have restarted faster than we polled
            yield _sse(phase="starting", detail="Agent restarting...")
        else:
            yield _sse(phase="starting", detail="Agent stopped; waiting for it to come back...")

        # Wait for the heartbeat to become fresh again (up to ~120s)
        for _ in range(240):
            if is_agent_alive() and not restart_requested():
                yield _sse(phase="ready", detail="Agent is back online.")
                return
            await asyncio.sleep(0.5)

        yield _sse(phase="timeout", detail="Timed out waiting for the agent to come back online.")

    return StreamingResponse(content=_events(), media_type="text/event-stream")


def _sse(phase: str, detail: str) -> str:
    """
    This function formats a Server-Sent Event with a JSON data payload.
    """
    import json

    return f"data: {json.dumps({'phase': phase, 'detail': detail})}\n\n"

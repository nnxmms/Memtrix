#!/usr/bin/python3

import logging
import os
import shutil
from typing import Any

from fastapi import APIRouter, HTTPException, status

from src.agents.provisioning import (
    AGENTS_DIR,
    AgentProvisionError,
    get_server_name,
    is_managed,
    provision_agent,
)
from src.core.config import CONFIG_PATH, load_config, update_config
from src.web.schemas import AgentCreate, AgentMeta, MessageResponse

logger: logging.Logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/meta", response_model=AgentMeta)
def get_meta() -> AgentMeta:
    """
    This endpoint reports whether the deployment runs on the bundled (managed) local
    homeserver or an external one, so the panel knows whether to collect Matrix
    credentials when creating a sub-agent.
    """
    config: dict[str, Any] = load_config()
    try:
        return AgentMeta(managed=is_managed(config=config), server_name=get_server_name(config=config))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not read homeserver configuration: {e}",
        )


@router.post("", response_model=MessageResponse)
def create_agent(body: AgentCreate) -> MessageResponse:
    """
    This endpoint provisions a new sub-agent end-to-end: it registers (or adopts) a
    Matrix account, scaffolds the workspace, and persists a complete config entry.
    The running agent picks it up on the next restart.
    """
    config: dict[str, Any] = load_config()
    try:
        slug, agent_config = provision_agent(
            config=config,
            name=body.name,
            description=body.description,
            model=body.model,
            matrix_user_id=body.matrix_user_id,
            matrix_access_token=body.matrix_access_token,
        )
    except AgentProvisionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.exception("Failed to provision sub-agent")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to provision sub-agent: {e}",
        )

    def _add(cfg: dict[str, Any]) -> None:
        cfg.setdefault("agents", {})[slug] = agent_config

    update_config(mutate=_add)

    logger.info("Provisioned sub-agent '%s' (%s) via web panel", slug, agent_config["matrix_user_id"])
    return MessageResponse(
        message=f"Sub-agent '{agent_config['display_name']}' created. Restart to bring it online."
    )


@router.delete("/{slug}", response_model=MessageResponse)
def delete_agent(slug: str) -> MessageResponse:
    """
    This endpoint removes a sub-agent: it deletes the config entry and its scaffolded
    workspace, memory index, and sessions. The Matrix account itself is left on the
    homeserver.
    """
    config: dict[str, Any] = load_config()
    agents: dict[str, Any] = config.get("agents") or {}
    if slug not in agents:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Sub-agent '{slug}' not found.")

    display_name: str = agents[slug].get("display_name", slug)

    def _remove(cfg: dict[str, Any]) -> None:
        (cfg.get("agents") or {}).pop(slug, None)

    update_config(mutate=_remove)

    # Best-effort filesystem cleanup (mirrors the agent runtime's delete_agent)
    data_dir: str = os.path.dirname(CONFIG_PATH)
    for path in (
        os.path.join(AGENTS_DIR, slug),
        os.path.join(data_dir, "memory_index", slug),
        os.path.join(data_dir, "sessions", slug),
    ):
        if os.path.isdir(path):
            try:
                shutil.rmtree(path=path)
            except OSError:
                logger.warning("Failed to remove '%s' while deleting sub-agent '%s'", path, slug)

    logger.info("Deleted sub-agent '%s' via web panel", slug)
    return MessageResponse(message=f"Sub-agent '{display_name}' deleted. Restart to apply.")

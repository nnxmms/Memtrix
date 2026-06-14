#!/usr/bin/python3

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, status

from src.config import load_config, save_config
from src.secrets import SECRET_PREFIX, read_managed_secrets
from src.verification import test_channel, test_provider, validate_config
from src.web.schemas import (
    ConfigPayload,
    MessageResponse,
    TestResult,
    TestTarget,
    ValidateResponse,
)

logger: logging.Logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(prefix="/api/config", tags=["config"])

# Top-level config keys the panel is allowed to replace
EDITABLE_SECTIONS: set[str] = {
    "main-agent",
    "agents",
    "providers",
    "models",
    "channels",
    "memory",
    "web",
    "workspace-directory",
    "registration_token",
    "secrets",
}


@router.get("")
def get_config() -> dict[str, Any]:
    """
    This endpoint returns the full configuration. Secret values are stored as
    $PLACEHOLDER references, not literals, so the document is safe to return.
    """
    return load_config()


@router.post("/validate", response_model=ValidateResponse)
def validate(payload: ConfigPayload) -> ValidateResponse:
    """
    This endpoint statically validates a candidate configuration without saving it.
    """
    errors: list[str] = validate_config(config=payload.config)
    return ValidateResponse(valid=not errors, errors=errors)


@router.put("", response_model=MessageResponse)
def put_config(payload: ConfigPayload) -> MessageResponse:
    """
    This endpoint validates and persists a full configuration document. Malformed
    configurations are rejected before anything is written to disk.
    """
    errors: list[str] = validate_config(config=payload.config)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"errors": errors},
        )
    save_config(config=payload.config)
    return MessageResponse(message="Configuration saved. Restart to apply.")


@router.put("/section/{section}", response_model=MessageResponse)
def put_section(section: str, body: dict[str, Any]) -> MessageResponse:
    """
    This endpoint replaces a single top-level config section, validates the merged
    result, and persists it only when valid.
    """
    if section not in EDITABLE_SECTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Section '{section}' is not editable.",
        )
    config: dict[str, Any] = load_config()
    config[section] = body.get("value", body)
    errors: list[str] = validate_config(config=config)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"errors": errors},
        )
    save_config(config=config)
    return MessageResponse(message=f"Section '{section}' saved. Restart to apply.")


@router.post("/test/provider", response_model=TestResult)
def test_provider_endpoint(target: TestTarget) -> TestResult:
    """
    This endpoint runs a live connectivity check against a provider configuration.
    """
    ok, detail = test_provider(provider_type=target.type, params=_resolve_params(params=target.params))
    return TestResult(ok=ok, detail=detail)


@router.post("/test/channel", response_model=TestResult)
def test_channel_endpoint(target: TestTarget) -> TestResult:
    """
    This endpoint runs a live connectivity check against a channel configuration.
    """
    ok, detail = test_channel(channel_type=target.type, params=_resolve_params(params=target.params))
    return TestResult(ok=ok, detail=detail)


def _resolve_params(params: dict[str, Any]) -> dict[str, Any]:
    """
    This function resolves any $PLACEHOLDER secret references in flat connectivity
    test parameters so live tests use real credentials. Unknown placeholders are
    left untouched (best-effort, never raises).
    """
    values: dict[str, str] = read_managed_secrets()
    resolved: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, str) and value.startswith("$") and len(value) > 1:
            name: str = value[1:]
            resolved[key] = values.get(name) or os.environ.get(SECRET_PREFIX + name, value)
        else:
            resolved[key] = value
    return resolved

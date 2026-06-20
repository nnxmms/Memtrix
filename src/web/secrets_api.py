#!/usr/bin/python3

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, status

from src.core.config import load_config
from src.integrations.secrets import (
    BITWARDEN_TOKEN_ENV,
    SECRET_PREFIX,
    read_managed_secrets,
    write_managed_secret,
)
from src.core.verification import test_bitwarden
from src.web.schemas import (
    BitwardenTest,
    MessageResponse,
    SecretInfo,
    SecretListResponse,
    SecretUpdate,
    TestResult,
)

logger: logging.Logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(prefix="/api/secrets", tags=["secrets"])


def _backend(config: dict[str, Any]) -> str:
    """
    This function returns the active secrets backend ("bitwarden" or "env").
    """
    return "bitwarden" if (config.get("secrets") or {}).get("backend") == "bitwarden" else "env"


def _collect_placeholders(obj: Any, acc: set[str]) -> None:
    """
    This function walks the config and collects every $PLACEHOLDER secret name.
    """
    if isinstance(obj, dict):
        for value in obj.values():
            _collect_placeholders(obj=value, acc=acc)
    elif isinstance(obj, list):
        for item in obj:
            _collect_placeholders(obj=item, acc=acc)
    elif isinstance(obj, str) and obj.startswith("$") and len(obj) > 1:
        acc.add(obj[1:])


def _bitwarden_client(config: dict[str, Any]) -> Any:
    """
    This function builds and connects a Bitwarden client using the configured
    organization/project and the access token from the environment or secrets file.
    """
    from src.integrations.bitwarden import BitwardenSecrets

    secrets_cfg: dict[str, Any] = config.get("secrets") or {}
    token: str | None = os.environ.get(BITWARDEN_TOKEN_ENV) or read_managed_secrets().get(BITWARDEN_TOKEN_ENV)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Bitwarden access token available.",
        )
    client: BitwardenSecrets = BitwardenSecrets(
        organization_id=secrets_cfg.get("organization_id"),
        project_id=secrets_cfg.get("project_id"),
        api_url=secrets_cfg.get("api_url"),
        identity_url=secrets_cfg.get("identity_url"),
    )
    client.connect(access_token=token)
    return client


def resolve_secret_map(config: dict[str, Any]) -> dict[str, str]:
    """
    This function returns a placeholder -> value mapping for every secret the
    config references, using the active backend. For Bitwarden it fetches all
    secrets; for the env backend it reads the managed secrets file and falls back
    to MEMTRIX_SECRET_* environment variables. Best-effort: failures yield an empty
    or partial map rather than raising, so callers degrade gracefully.
    """
    backend: str = _backend(config=config)

    placeholders: set[str] = set()
    _collect_placeholders(obj=config, acc=placeholders)

    values: dict[str, str] = {}
    if backend == "bitwarden":
        try:
            client: Any = _bitwarden_client(config=config)
            values = client.fetch_all()
        except HTTPException:
            values = {}
        except Exception as exc:
            logger.error("Failed to fetch Bitwarden secrets: %s", exc)
            values = {}
    else:
        values = read_managed_secrets()
        # Fall back to environment for placeholders not in the managed file
        for name in placeholders:
            if name not in values:
                env_value: str | None = os.environ.get(SECRET_PREFIX + name)
                if env_value is not None:
                    values[name] = env_value
    return values


@router.get("", response_model=SecretListResponse)
def list_secrets() -> SecretListResponse:
    """
    This endpoint returns every secret referenced by the config along with its
    current value. Values are returned decrypted; the client masks them by default.
    """
    config: dict[str, Any] = load_config()
    backend: str = _backend(config=config)

    placeholders: set[str] = set()
    _collect_placeholders(obj=config, acc=placeholders)

    values: dict[str, str] = resolve_secret_map(config=config)

    secrets: list[SecretInfo] = []
    for name in sorted(placeholders):
        secrets.append(SecretInfo(key=name, value=values.get(name, ""), backend=backend))
    return SecretListResponse(backend=backend, secrets=secrets)


@router.put("/{key}", response_model=MessageResponse)
def set_secret(key: str, body: SecretUpdate) -> MessageResponse:
    """
    This endpoint sets or changes a secret value in the active backend. The change
    takes effect on the next agent restart.
    """
    config: dict[str, Any] = load_config()
    backend: str = _backend(config=config)

    if backend == "bitwarden":
        client: Any = _bitwarden_client(config=config)
        try:
            client.upsert_secret(key=key, value=body.value, note=body.note)
        except Exception as exc:
            logger.error("Failed to upsert Bitwarden secret '%s': %s", key, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Bitwarden update failed: {exc}",
            )
        return MessageResponse(message=f"Secret '{key}' updated in Bitwarden. Restart to apply.")

    write_managed_secret(placeholder=key, value=body.value)
    return MessageResponse(message=f"Secret '{key}' saved. Restart to apply.")


@router.post("/test/bitwarden", response_model=TestResult)
def test_bitwarden_endpoint(body: BitwardenTest) -> TestResult:
    """
    This endpoint verifies a Bitwarden access token and organization.
    """
    ok, detail = test_bitwarden(
        access_token=body.access_token,
        organization_id=body.organization_id,
        project_id=body.project_id,
        api_url=body.api_url,
        identity_url=body.identity_url,
    )
    return TestResult(ok=ok, detail=detail)

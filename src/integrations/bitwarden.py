#!/usr/bin/python3

import logging
from typing import Any

logger: logging.Logger = logging.getLogger(__name__)

# Default Bitwarden cloud endpoints (EU region)
DEFAULT_API_URL: str = "https://api.bitwarden.eu"
DEFAULT_IDENTITY_URL: str = "https://identity.bitwarden.eu"


def is_enabled(config: dict[str, Any]) -> bool:
    """
    This function returns True if the config requests the Bitwarden secrets backend.
    """
    secrets_cfg: dict[str, Any] = config.get("secrets", {})
    return secrets_cfg.get("backend") == "bitwarden"


class BitwardenSecrets:

    def __init__(
        self,
        organization_id: str | None = None,
        project_id: str | None = None,
        api_url: str | None = None,
        identity_url: str | None = None,
    ) -> None:
        """
        This is a thin wrapper around the Bitwarden Secrets Manager SDK used to
        fetch and create secrets for Memtrix.
        """
        self._organization_id: str | None = organization_id
        self._project_id: str | None = project_id
        self._api_url: str = api_url or DEFAULT_API_URL
        self._identity_url: str = identity_url or DEFAULT_IDENTITY_URL
        self._client: Any = None
        self._login_response: Any = None

    def connect(self, access_token: str) -> None:
        """
        This function creates the SDK client and authenticates with the access token.
        Raises RuntimeError on failure (including when the SDK is not installed).
        """
        try:
            from bitwarden_sdk import (
                BitwardenClient,
                DeviceType,
                client_settings_from_dict,
            )
        except ImportError as exc:
            raise RuntimeError(
                "Bitwarden backend is enabled but the 'bitwarden-sdk' package is not installed."
            ) from exc

        self._client = BitwardenClient(
            client_settings_from_dict(
                {
                    "apiUrl": self._api_url,
                    "identityUrl": self._identity_url,
                    "deviceType": DeviceType.SDK,
                    "userAgent": "Memtrix",
                }
            )
        )

        try:
            # state file is intentionally None: re-authenticate each run so we
            # never write SDK state onto the read-only container filesystem.
            self._login_response = self._client.auth().login_access_token(access_token, None)
        except Exception as exc:
            raise RuntimeError(f"Bitwarden authentication failed: {exc}") from exc

    def set_organization_id(self, organization_id: str) -> None:
        """
        This function sets the organization ID used for subsequent calls.
        """
        self._organization_id = organization_id

    def set_project_id(self, project_id: str) -> None:
        """
        This function sets the project ID that new secrets are stored in.
        """
        self._project_id = project_id

    def detect_organization_id(self) -> str | None:
        """
        This function makes a best-effort attempt to read the organization ID from
        the access-token login response. Secrets Manager tokens are scoped to a
        single organization, but the SDK does not reliably expose it, so this may
        return None and the caller should fall back to asking the user.
        """
        response: Any = self._login_response
        if response is None:
            return None
        candidates: list[Any] = [response, getattr(response, "data", None)]
        for source in candidates:
            if source is None:
                continue
            for attr in ("organization_id", "organizationId", "organization"):
                value: Any = getattr(source, attr, None)
                if value:
                    return str(value)
        return None

    def test_connection(self) -> bool:
        """
        This function verifies the client can reach the organization's secrets.
        """
        try:
            self._client.secrets().list(self._organization_id)
            return True
        except Exception as exc:
            logger.error("Bitwarden connection test failed: %s", exc)
            return False

    def list_projects(self) -> list[tuple[str, str]]:
        """
        This function returns a list of (id, name) tuples for the organization's projects.
        """
        response: Any = self._client.projects().list(self._organization_id)
        return [(str(p.id), str(p.name)) for p in response.data.data]

    def fetch_all(self) -> dict[str, str]:
        """
        This function fetches every secret the access token can read and returns
        a mapping of secret key -> secret value.
        """
        listing: Any = self._client.secrets().list(self._organization_id)
        ids: list[str] = [item.id for item in listing.data.data]
        if not ids:
            return {}
        retrieved: Any = self._client.secrets().get_by_ids(ids)
        return {item.key: item.value for item in retrieved.data.data}

    def create_secret(self, key: str, value: str, note: str = "") -> None:
        """
        This function creates a single secret in the configured project.
        """
        project_ids: list[str] = [self._project_id] if self._project_id else []
        # SDK signature is create(organization_id, key, value, note, project_ids).
        self._client.secrets().create(
            self._organization_id,
            key,
            value,
            note,
            project_ids,
        )

    def upsert_secret(self, key: str, value: str, note: str = "") -> None:
        """
        This function updates an existing secret with the given key, or creates it
        when no secret with that key exists yet.
        """
        listing: Any = self._client.secrets().list(self._organization_id)
        existing_id: str | None = None
        for item in listing.data.data:
            if str(item.key) == key:
                existing_id = str(item.id)
                break

        if existing_id is None:
            self.create_secret(key=key, value=value, note=note)
            return

        project_ids: list[str] = [self._project_id] if self._project_id else []
        # SDK signature is update(organization_id, id, key, value, note, project_ids).
        self._client.secrets().update(
            self._organization_id,
            existing_id,
            key,
            value,
            note,
            project_ids,
        )


def load_bitwarden_secrets(config: dict[str, Any]) -> dict[str, str]:
    """
    This function connects to Bitwarden using the BWS_ACCESS_TOKEN environment
    variable and returns a mapping of secret key -> value for the organization.
    """
    import os

    access_token: str | None = os.environ.get("BWS_ACCESS_TOKEN")
    if not access_token:
        raise RuntimeError(
            "Bitwarden backend is enabled but environment variable 'BWS_ACCESS_TOKEN' is not set."
        )

    secrets_cfg: dict[str, Any] = config.get("secrets", {})
    organization_id: str | None = secrets_cfg.get("organization_id")
    if not organization_id:
        raise RuntimeError("Bitwarden backend is enabled but 'secrets.organization_id' is missing from config.")

    client: BitwardenSecrets = BitwardenSecrets(
        organization_id=organization_id,
        project_id=secrets_cfg.get("project_id"),
        api_url=secrets_cfg.get("api_url"),
        identity_url=secrets_cfg.get("identity_url"),
    )
    client.connect(access_token=access_token)
    return client.fetch_all()

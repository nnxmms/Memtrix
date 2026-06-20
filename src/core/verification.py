#!/usr/bin/python3

import logging
from typing import Any

import requests

logger: logging.Logger = logging.getLogger(__name__)

# Provider types and the parameter keys they require
PROVIDER_REQUIRED_PARAMS: dict[str, list[str]] = {
    "ollama": ["base_url"],
    "openrouter": ["api_key"],
}

# Channel types and the parameter keys they require
CHANNEL_REQUIRED_PARAMS: dict[str, list[str]] = {
    "matrix": ["homeserver", "user_id", "access_token"],
    "cli": [],
}

# Default network timeout for live connectivity checks
TEST_TIMEOUT: int = 15


def validate_config(config: dict[str, Any]) -> list[str]:
    """
    This function statically validates a Memtrix config and returns a list of
    human-readable error strings. An empty list means the config is structurally
    valid. It does NOT perform any network calls.
    """
    errors: list[str] = []

    providers: dict[str, Any] = config.get("providers") or {}
    models: dict[str, Any] = config.get("models") or {}
    channels: dict[str, Any] = config.get("channels") or {}

    if not isinstance(providers, dict) or not providers:
        errors.append("At least one provider must be configured.")
    if not isinstance(models, dict) or not models:
        errors.append("At least one model must be configured.")
    if not isinstance(channels, dict) or not channels:
        errors.append("At least one channel must be configured.")

    # Validate each provider
    for name, provider in (providers if isinstance(providers, dict) else {}).items():
        if not isinstance(provider, dict):
            errors.append(f"Provider '{name}' must be an object.")
            continue
        ptype: Any = provider.get("type")
        if not ptype:
            errors.append(f"Provider '{name}' is missing a 'type'.")
            continue
        if ptype not in PROVIDER_REQUIRED_PARAMS:
            errors.append(f"Provider '{name}' has unknown type '{ptype}'.")
            continue
        for key in PROVIDER_REQUIRED_PARAMS[ptype]:
            if not provider.get(key):
                errors.append(f"Provider '{name}' ({ptype}) is missing '{key}'.")

    # Validate each model and its provider reference
    for name, model in (models if isinstance(models, dict) else {}).items():
        if not isinstance(model, dict):
            errors.append(f"Model '{name}' must be an object.")
            continue
        if not model.get("model"):
            errors.append(f"Model '{name}' is missing the 'model' name.")
        provider_ref: Any = model.get("provider")
        if not provider_ref:
            errors.append(f"Model '{name}' is missing a 'provider'.")
        elif provider_ref not in providers:
            errors.append(f"Model '{name}' references unknown provider '{provider_ref}'.")

    # Validate each channel
    for name, channel in (channels if isinstance(channels, dict) else {}).items():
        if not isinstance(channel, dict):
            errors.append(f"Channel '{name}' must be an object.")
            continue
        ctype: Any = channel.get("type")
        if not ctype:
            errors.append(f"Channel '{name}' is missing a 'type'.")
            continue
        if ctype not in CHANNEL_REQUIRED_PARAMS:
            errors.append(f"Channel '{name}' has unknown type '{ctype}'.")
            continue
        for key in CHANNEL_REQUIRED_PARAMS[ctype]:
            if not channel.get(key):
                errors.append(f"Channel '{name}' ({ctype}) is missing '{key}'.")

    # Validate the main agent and any sub-agents reference real models/channels
    errors.extend(_validate_agent(label="main-agent", agent=config.get("main-agent"),
                                  models=models, channels=channels, required=True))
    for name, agent in (config.get("agents") or {}).items():
        errors.extend(_validate_agent(label=f"agent '{name}'", agent=agent,
                                      models=models, channels=channels, required=True))

    # Validate optional voice-transcription settings
    voice: dict[str, Any] = config.get("voice") or {}
    if not isinstance(voice, dict):
        errors.append("voice section must be an object when present.")
        return errors

    provider: Any = voice.get("provider", "local")
    if provider not in ("local",):
        errors.append("voice.provider must be 'local'.")

    model: Any = voice.get("model", "base")
    if not isinstance(model, str) or not model.strip():
        errors.append("voice.model must be a non-empty string.")

    max_audio_bytes: Any = voice.get("max_audio_bytes", 25_000_000)
    if not isinstance(max_audio_bytes, int) or max_audio_bytes <= 0:
        errors.append("voice.max_audio_bytes must be a positive integer.")

    timeout_seconds: Any = voice.get("timeout_seconds", 180)
    if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        errors.append("voice.timeout_seconds must be a positive integer.")

    return errors


def _validate_agent(label: str, agent: Any, models: dict[str, Any],
                    channels: dict[str, Any], required: bool) -> list[str]:
    """
    This function validates a single agent section's model and channel references.
    """
    errors: list[str] = []
    if not isinstance(agent, dict):
        if required:
            errors.append(f"{label} section is missing or invalid.")
        return errors

    model_ref: Any = agent.get("model")
    channel_ref: Any = agent.get("channel")
    if not model_ref:
        errors.append(f"{label} is missing a 'model'.")
    elif model_ref not in models:
        errors.append(f"{label} references unknown model '{model_ref}'.")
    if not channel_ref:
        errors.append(f"{label} is missing a 'channel'.")
    elif channel_ref not in channels:
        errors.append(f"{label} references unknown channel '{channel_ref}'.")
    return errors


def test_provider(provider_type: str, params: dict[str, Any]) -> tuple[bool, str]:
    """
    This function performs a live connectivity check against a provider using the
    given resolved parameters. Returns (ok, detail).
    """
    try:
        if provider_type == "ollama":
            base_url: str = str(params.get("base_url", "")).rstrip("/")
            if not base_url:
                return False, "Missing base_url."
            response: requests.Response = requests.get(url=f"{base_url}/api/tags", timeout=TEST_TIMEOUT)
            if response.status_code == 200:
                models: list[Any] = response.json().get("models", [])
                return True, f"Reachable — {len(models)} model(s) available."
            return False, f"Ollama returned HTTP {response.status_code}."

        if provider_type == "openrouter":
            api_key: str = str(params.get("api_key", ""))
            if not api_key:
                return False, "Missing api_key."
            response = requests.get(
                url="https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=TEST_TIMEOUT,
            )
            if response.status_code == 200:
                return True, "API key accepted."
            if response.status_code in (401, 403):
                return False, "API key rejected (unauthorized)."
            return False, f"OpenRouter returned HTTP {response.status_code}."

        return False, f"Unknown provider type '{provider_type}'."
    except requests.exceptions.RequestException as exc:
        return False, f"Connection failed: {exc}"


def test_matrix(homeserver: str, access_token: str) -> tuple[bool, str]:
    """
    This function verifies Matrix credentials by calling /whoami. Returns
    (ok, resolved_user_id_or_error).
    """
    try:
        url: str = f"{homeserver.rstrip('/')}/_matrix/client/v3/account/whoami"
        response: requests.Response = requests.get(
            url=url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=TEST_TIMEOUT,
        )
        if response.status_code == 200:
            user_id: str = response.json().get("user_id", "")
            return True, user_id or "Authenticated."
        if response.status_code in (401, 403):
            return False, "Access token rejected (unauthorized)."
        return False, f"Homeserver returned HTTP {response.status_code}."
    except requests.exceptions.RequestException as exc:
        return False, f"Connection failed: {exc}"


def test_channel(channel_type: str, params: dict[str, Any]) -> tuple[bool, str]:
    """
    This function performs a live connectivity check against a channel using the
    given resolved parameters. Returns (ok, detail).
    """
    if channel_type == "cli":
        return True, "CLI channel requires no connectivity check."
    if channel_type == "matrix":
        return test_matrix(
            homeserver=str(params.get("homeserver", "")),
            access_token=str(params.get("access_token", "")),
        )
    return False, f"Unknown channel type '{channel_type}'."


def test_bitwarden(access_token: str, organization_id: str | None = None,
                   project_id: str | None = None, api_url: str | None = None,
                   identity_url: str | None = None) -> tuple[bool, str]:
    """
    This function verifies a Bitwarden Secrets Manager access token by connecting
    and listing secrets. Returns (ok, detail).
    """
    from src.integrations.bitwarden import BitwardenSecrets

    try:
        client: BitwardenSecrets = BitwardenSecrets(
            organization_id=organization_id,
            project_id=project_id,
            api_url=api_url,
            identity_url=identity_url,
        )
        client.connect(access_token=access_token)
        if organization_id is None:
            detected: str | None = client.detect_organization_id()
            if detected is None:
                return False, "Connected, but organization_id could not be determined."
            client.set_organization_id(organization_id=detected)
        if not client.test_connection():
            return False, "Connected, but could not list secrets for the organization."
        return True, "Bitwarden access token verified."
    except Exception as exc:
        return False, f"Bitwarden verification failed: {exc}"

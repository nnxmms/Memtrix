#!/usr/bin/python3

import os
from typing import Any

# Prefix for all Memtrix secret environment variables
SECRET_PREFIX: str = "MEMTRIX_SECRET_"

# Environment variable holding the Bitwarden machine-account access token
BITWARDEN_TOKEN_ENV: str = "BWS_ACCESS_TOKEN"


def resolve_secrets(config: dict[str, Any], bitwarden: dict[str, str] | None = None) -> dict[str, Any]:
    """
    This function walks the config dict and replaces any string value starting
    with $ with the corresponding secret value.
    When a Bitwarden secret map is supplied, placeholders resolve from it first,
    then fall back to the corresponding environment variable.
    E.g. "$MATRIX_ACCESS_TOKEN" -> bitwarden["MATRIX_ACCESS_TOKEN"]
                                or os.environ["MEMTRIX_SECRET_MATRIX_ACCESS_TOKEN"]
    """
    return _resolve_recursive(obj=config, bitwarden=bitwarden)


def clear_secrets_from_env() -> None:
    """
    This function removes all MEMTRIX_SECRET_* variables and the Bitwarden access
    token from the process environment so they cannot be leaked via shell commands
    like `env` or /proc/self/environ.
    """
    keys_to_remove: list[str] = [k for k in os.environ if k.startswith(SECRET_PREFIX)]
    for key in keys_to_remove:
        del os.environ[key]
    if BITWARDEN_TOKEN_ENV in os.environ:
        del os.environ[BITWARDEN_TOKEN_ENV]


def get_sanitized_env() -> dict[str, str]:
    """
    This function returns a copy of the environment with all MEMTRIX_SECRET_* variables
    and the Bitwarden access token removed.
    Used by run_command to prevent secret leakage via subprocesses.
    """
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(SECRET_PREFIX) and k != BITWARDEN_TOKEN_ENV
    }


# Optional secret keys that resolve to empty string if not set
OPTIONAL_SECRETS: set[str] = {"REGISTRATION_TOKEN"}


def _resolve_recursive(obj: Any, bitwarden: dict[str, str] | None = None) -> Any:
    """
    This function recursively resolves $PLACEHOLDER references in config values.
    Resolution order: Bitwarden secret map -> environment variable -> optional empty.
    """
    if isinstance(obj, dict):
        return {k: _resolve_recursive(obj=v, bitwarden=bitwarden) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_recursive(obj=item, bitwarden=bitwarden) for item in obj]
    if isinstance(obj, str) and obj.startswith("$"):
        placeholder: str = obj[1:]

        # Prefer the Bitwarden secret map when available
        if bitwarden is not None and placeholder in bitwarden:
            return bitwarden[placeholder]

        # Fall back to the corresponding environment variable
        env_key: str = SECRET_PREFIX + placeholder
        value: str | None = os.environ.get(env_key)
        if value is None:
            if placeholder in OPTIONAL_SECRETS:
                return ""
            raise RuntimeError(f"Missing secret: '{placeholder}' not found in Bitwarden or environment variable '{env_key}'.")
        return value
    return obj

#!/usr/bin/python3

import os
from typing import Any

# Prefix for all Memtrix secret environment variables
SECRET_PREFIX: str = "MEMTRIX_SECRET_"


def resolve_secrets(config: dict[str, Any]) -> dict[str, Any]:
    """
    This function walks the config dict and replaces any string value starting
    with $ with the corresponding environment variable value.
    E.g. "$MATRIX_ACCESS_TOKEN" -> os.environ["MEMTRIX_SECRET_MATRIX_ACCESS_TOKEN"]
    """
    return _resolve_recursive(obj=config)


def clear_secrets_from_env() -> None:
    """
    This function removes all MEMTRIX_SECRET_* variables from the process environment
    so they cannot be leaked via shell commands like `env` or /proc/self/environ.
    """
    keys_to_remove: list[str] = [k for k in os.environ if k.startswith(SECRET_PREFIX)]
    for key in keys_to_remove:
        del os.environ[key]


def get_sanitized_env() -> dict[str, str]:
    """
    This function returns a copy of the environment with all MEMTRIX_SECRET_* variables removed.
    Used by run_command to prevent secret leakage via subprocesses.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith(SECRET_PREFIX)}


# Optional secret keys that resolve to empty string if not set
OPTIONAL_SECRETS: set[str] = {"REGISTRATION_TOKEN"}


def _resolve_recursive(obj: Any) -> Any:
    """
    This function recursively resolves $PLACEHOLDER references in config values.
    """
    if isinstance(obj, dict):
        return {k: _resolve_recursive(obj=v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_recursive(obj=item) for item in obj]
    if isinstance(obj, str) and obj.startswith("$"):
        placeholder: str = obj[1:]
        env_key: str = SECRET_PREFIX + placeholder
        value: str | None = os.environ.get(env_key)
        if value is None:
            if placeholder in OPTIONAL_SECRETS:
                return ""
            raise RuntimeError(f"Missing secret: environment variable '{env_key}' is not set.")
        return value
    return obj

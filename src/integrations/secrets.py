#!/usr/bin/python3

import os
import tempfile
from typing import Any

from src.core.config import CONFIG_PATH

# Prefix for all Memtrix secret environment variables
SECRET_PREFIX: str = "MEMTRIX_SECRET_"

# Environment variable holding the Bitwarden machine-account access token
BITWARDEN_TOKEN_ENV: str = "BWS_ACCESS_TOKEN"

# Writable secrets file on the data volume. The compose env_file is only read at
# container creation, so secrets edited through the web panel are persisted here
# and re-read on every (re)start of the agent process.
MANAGED_SECRETS_PATH: str = os.path.join(os.path.dirname(CONFIG_PATH), "secrets.env")


def load_secrets_file(path: str = MANAGED_SECRETS_PATH) -> None:
    """
    This function loads KEY=VALUE pairs from the managed secrets file into the
    process environment. Values already present in the environment are NOT
    overridden, so compose env_file / real environment variables take precedence.
    """
    if not os.path.isfile(path):
        return
    for key, value in _parse_env_file(path=path).items():
        os.environ.setdefault(key, value)


def read_managed_secrets() -> dict[str, str]:
    """
    This function returns the managed secrets as a placeholder -> value mapping,
    stripping the MEMTRIX_SECRET_ prefix. The Bitwarden access token is returned
    under its own key. Used by the web panel to display current secret values.
    """
    raw: dict[str, str] = _parse_env_file(path=MANAGED_SECRETS_PATH)
    result: dict[str, str] = {}
    for key, value in raw.items():
        if key.startswith(SECRET_PREFIX):
            result[key[len(SECRET_PREFIX):]] = value
        elif key == BITWARDEN_TOKEN_ENV:
            result[BITWARDEN_TOKEN_ENV] = value
    return result


def write_managed_secret(placeholder: str, value: str) -> None:
    """
    This function upserts a single secret in the managed secrets file. The
    placeholder is stored prefixed with MEMTRIX_SECRET_ unless it is the Bitwarden
    access token, which keeps its own name. The new value takes effect on the next
    agent restart.
    """
    key: str = placeholder if placeholder == BITWARDEN_TOKEN_ENV else SECRET_PREFIX + placeholder
    existing: dict[str, str] = _parse_env_file(path=MANAGED_SECRETS_PATH)
    existing[key] = value
    _write_env_file(path=MANAGED_SECRETS_PATH, values=existing)


def _parse_env_file(path: str) -> dict[str, str]:
    """
    This function parses a simple KEY=VALUE env file, ignoring blank lines and
    comments. Surrounding quotes on values are stripped.
    """
    values: dict[str, str] = {}
    if not os.path.isfile(path):
        return values
    with open(file=path, mode="r") as f:
        for line in f:
            stripped: str = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, raw_value = stripped.partition("=")
            key = key.strip()
            raw_value = raw_value.strip()
            if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in ("'", '"'):
                raw_value = raw_value[1:-1]
            if key:
                values[key] = raw_value
    return values


def _write_env_file(path: str, values: dict[str, str]) -> None:
    """
    This function atomically writes a KEY=VALUE env file with restrictive
    permissions (0600) so secrets are not world-readable.
    """
    directory: str = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    lines: list[str] = [f'{key}="{value}"' for key, value in values.items()]
    content: str = "\n".join(lines) + ("\n" if lines else "")
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".secrets.", suffix=".tmp")
    try:
        with os.fdopen(fd, mode="w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(src=tmp_path, dst=path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


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


# Optional secret keys that resolve to empty string if not set
OPTIONAL_SECRETS: set[str] = {"REGISTRATION_TOKEN", "EMAIL_PASSWORD"}


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

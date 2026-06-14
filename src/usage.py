#!/usr/bin/python3

import logging
from typing import Any

import requests

logger: logging.Logger = logging.getLogger(__name__)

# OpenRouter endpoint that returns rate-limit and credit usage for an API key
OPENROUTER_KEY_URL: str = "https://openrouter.ai/api/v1/key"

# Default network timeout for usage lookups
USAGE_TIMEOUT: int = 15


def fetch_openrouter_key_info(api_key: str, timeout: int = USAGE_TIMEOUT) -> dict[str, Any]:
    """
    This function fetches credit/usage information for an OpenRouter API key.
    It returns the "data" object documented at
    https://openrouter.ai/docs/api/reference/limits — including usage_daily
    (credits used in the current UTC day, where one credit equals one US dollar).
    Raises requests.exceptions.RequestException on network errors and RuntimeError
    on an unexpected response.
    """
    response: requests.Response = requests.get(
        url=OPENROUTER_KEY_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )
    if response.status_code in (401, 403):
        raise RuntimeError("API key rejected (unauthorized).")
    if response.status_code != 200:
        raise RuntimeError(f"OpenRouter returned HTTP {response.status_code}.")
    payload: Any = response.json()
    data: Any = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected response from OpenRouter.")
    return data


def _money(value: Any) -> str:
    """
    This function formats a credit amount (US dollars) with four decimal places,
    falling back gracefully when the value is missing or non-numeric.
    """
    try:
        return f"${float(value):.4f}"
    except (TypeError, ValueError):
        return "n/a"


def format_costs(providers: dict[str, Any], timeout: int = USAGE_TIMEOUT) -> str:
    """
    This function builds a human-readable cost report for every configured
    OpenRouter provider, deduplicating by API key. For each key it reports the
    credits used today (current UTC day), all-time usage, and any configured
    credit limit / remaining balance.
    """
    # Collect OpenRouter providers, grouping instance labels by their API key so a
    # shared key is only queried once.
    by_key: dict[str, list[str]] = {}
    for label, provider in (providers or {}).items():
        if not isinstance(provider, dict) or provider.get("type") != "openrouter":
            continue
        api_key: str = str(provider.get("api_key") or "")
        if not api_key:
            continue
        by_key.setdefault(api_key, []).append(label)

    if not by_key:
        return "No OpenRouter providers are configured, so there are no costs to report."

    blocks: list[str] = []
    for api_key, labels in by_key.items():
        heading: str = ", ".join(sorted(labels))
        try:
            info: dict[str, Any] = fetch_openrouter_key_info(api_key=api_key, timeout=timeout)
        except RuntimeError as exc:
            blocks.append(f"OpenRouter usage ({heading}):\n  Error: {exc}")
            continue
        except requests.exceptions.RequestException as exc:
            blocks.append(f"OpenRouter usage ({heading}):\n  Couldn't reach OpenRouter: {exc}")
            continue

        lines: list[str] = [f"OpenRouter usage ({heading}):"]
        lines.append(f"  Today (UTC):  {_money(info.get('usage_daily'))}")
        lines.append(f"  This week:    {_money(info.get('usage_weekly'))}")
        lines.append(f"  This month:   {_money(info.get('usage_monthly'))}")
        lines.append(f"  All-time:     {_money(info.get('usage'))}")

        limit: Any = info.get("limit")
        if limit is not None:
            remaining: Any = info.get("limit_remaining")
            if remaining is not None:
                lines.append(f"  Limit:        {_money(limit)}  (remaining {_money(remaining)})")
            else:
                lines.append(f"  Limit:        {_money(limit)}")
        else:
            lines.append("  Limit:        unlimited")

        if info.get("is_free_tier"):
            lines.append("  Tier:         free (no credits purchased)")

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)

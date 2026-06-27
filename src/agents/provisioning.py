#!/usr/bin/python3

import logging
import os
import re
import secrets
import shutil
import string
from typing import Any

import requests

logger: logging.Logger = logging.getLogger(__name__)

# Valid agent name: letters, spaces, hyphens. 2–24 chars.
AGENT_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z][A-Za-z \-]{1,23}$")

# Agents root directory inside the container
AGENTS_DIR: str = "/home/memtrix/agents"

# Fallback Matrix server name when it cannot be derived from the bot user ID
DEFAULT_SERVER_NAME: str = "memtrix.local"

# Default core file templates for new agents
SOUL_TEMPLATE: str = """## Soul

You are **{display_name}**, a specialist sub-agent of the {main_name} system.

Your expertise: **{description}**

You exist to provide deep, focused knowledge in your domain. You have your own memory, your own personality, and your own conversation history — separate from the main {main_name} agent and any other sub-agents.

You value accuracy in your domain above all else. If you're unsure about something, say so. If a question falls outside your expertise, be honest about your limits.

You remember conversations and learn from your user over time, just like the main agent — but through the lens of your specialty.
"""

BEHAVIOR_TEMPLATE: str = """- Keep it focused. Stay within your area of expertise unless asked otherwise.
- Be direct and specific. Domain experts don't need fluff.
- Match the user's language. If they write German, respond in German.
- Have strong opinions in your domain. Push back when something seems off.
- Ask clarifying questions when the user's request is ambiguous in your domain.
"""


class AgentProvisionError(Exception):
    """
    Raised when a sub-agent cannot be provisioned. The message is user-facing and
    safe to surface directly to the operator (in chat or in the web panel).
    """


def generate_password(length: int = 24) -> str:
    """
    This function generates a random password for Matrix user registration.
    """
    alphabet: str = string.ascii_letters + string.digits
    return "".join(secrets.choice(seq=alphabet) for _ in range(length))


def get_main_channel(config: dict[str, Any]) -> dict[str, Any]:
    """
    This function returns the main agent's channel config section.
    """
    channel_name: str = config["main-agent"]["channel"]
    return config["channels"][channel_name]


def get_homeserver(config: dict[str, Any]) -> str:
    """
    This function returns the Matrix homeserver URL from the main agent's channel config.
    """
    return get_main_channel(config=config)["homeserver"]


def is_managed(config: dict[str, Any]) -> bool:
    """
    This function reports whether the main channel uses the bundled local Conduit
    homeserver (managed) or an external/already-hosted server. Managed servers
    support automatic user registration via the registration token.
    """
    return bool(get_main_channel(config=config).get("managed", True))


def get_server_name(config: dict[str, Any]) -> str:
    """
    This function derives the Matrix server name (the part after ':' in user IDs)
    from the main agent's bot user ID, e.g. '@memtrix:matrix.org' -> 'matrix.org'.
    Falls back to the default local Conduit server name.
    """
    user_id: str = get_main_channel(config=config).get("user_id", "")
    if ":" in user_id:
        return user_id.split(sep=":", maxsplit=1)[1]
    return DEFAULT_SERVER_NAME


def register_matrix_user(config: dict[str, Any], homeserver: str, username: str, password: str) -> dict[str, Any]:
    """
    This function registers a user on the Matrix homeserver via the Client-Server API.
    Uses the registration token from config for token-protected registration.
    """
    url: str = f"{homeserver.rstrip('/')}/_matrix/client/v3/register"
    body: dict[str, Any] = {
        "username": username,
        "password": password,
        "auth": {"type": "m.login.dummy"},
        "inhibit_login": False,
    }

    # Use registration token if available
    reg_token: str = config.get("registration_token", "")
    if reg_token:
        body["auth"] = {
            "type": "m.login.registration_token",
            "token": reg_token,
        }

    response: requests.Response = requests.post(url=url, json=body, timeout=30)
    response.raise_for_status()
    return response.json()


def set_display_name(homeserver: str, user_id: str, access_token: str, display_name: str) -> None:
    """
    This function sets a user's display name on the Matrix homeserver.
    """
    url: str = f"{homeserver.rstrip('/')}/_matrix/client/v3/profile/{requests.utils.quote(user_id)}/displayname"
    requests.put(
        url=url,
        headers={"Authorization": f"Bearer {access_token}"},
        json={"displayname": display_name},
        timeout=10,
    )


def scaffold_workspace(config: dict[str, Any], name: str, description: str, display_name: str) -> str:
    """
    This function creates the workspace directory structure for a new sub-agent.
    Returns the workspace directory path.
    """
    workspace_dir: str = os.path.join(AGENTS_DIR, name)
    os.makedirs(name=workspace_dir, exist_ok=True)
    os.makedirs(name=os.path.join(workspace_dir, "memory"), exist_ok=True)
    os.makedirs(name=os.path.join(workspace_dir, "attachments"), exist_ok=True)
    os.makedirs(name=os.path.join(workspace_dir, "downloads"), exist_ok=True)

    # Copy AGENT.md from static templates (src/static, one level up from src/agents)
    src_agent_md: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "AGENT.md")
    dst_agent_md: str = os.path.join(workspace_dir, "AGENT.md")
    if os.path.isfile(path=src_agent_md):
        shutil.copy2(src=src_agent_md, dst=dst_agent_md)

        # Replace the opening line with sub-agent identity
        with open(file=dst_agent_md, mode="r") as f:
            content: str = f.read()
        main_name: str = config["main-agent"].get("name", "Memtrix")
        content = content.replace(
            "You are **Memtrix**, a personal AI assistant.",
            f"You are **{display_name}**, a specialist sub-agent of {main_name}.\n\nYour expertise: **{description}**",
        )

        # Remove the Sub-Agents section — sub-agents cannot manage other agents
        content = re.sub(
            r"\n---\n\n## Sub-Agents\n.*?(?=\n---\n)",
            "",
            content,
            flags=re.DOTALL,
        )

        with open(file=dst_agent_md, mode="w") as f:
            f.write(content)

    # Write customized core files
    main_name: str = config["main-agent"].get("name", "Memtrix")
    with open(file=os.path.join(workspace_dir, "SOUL.md"), mode="w") as f:
        f.write(SOUL_TEMPLATE.format(display_name=display_name, description=description, main_name=main_name))

    # Copy BEHAVIOR.md from main agent's workspace (inherits user's customizations)
    main_behavior: str = os.path.join(config["workspace-directory"], "BEHAVIOR.md")
    if os.path.isfile(path=main_behavior):
        shutil.copy2(src=main_behavior, dst=os.path.join(workspace_dir, "BEHAVIOR.md"))
    else:
        with open(file=os.path.join(workspace_dir, "BEHAVIOR.md"), mode="w") as f:
            f.write(BEHAVIOR_TEMPLATE)

    # Symlink USER.md to the main agent's copy (shared across all agents)
    main_user: str = os.path.join(config["workspace-directory"], "USER.md")
    user_link: str = os.path.join(workspace_dir, "USER.md")
    if not os.path.islink(user_link) and not os.path.exists(user_link):
        os.symlink(src=main_user, dst=user_link)

    return workspace_dir


def provision_agent(
    config: dict[str, Any],
    *,
    name: str,
    description: str,
    model: str = "",
    matrix_user_id: str = "",
    matrix_access_token: str = "",
) -> tuple[str, dict[str, Any]]:
    """
    This function provisions a new sub-agent: it validates the request, registers
    (or adopts) the Matrix account, and scaffolds the workspace. It returns the
    agent's config slug and a complete agent-config dict ready to be persisted.

    It does NOT persist config or start the agent thread — those are the caller's
    responsibility, so the same flow can be driven from the agent runtime or the
    web control panel. Raises AgentProvisionError with a user-facing message on any
    validation or registration failure.
    """
    name = (name or "").strip()
    description = (description or "").strip()
    model = (model or "").strip()

    if not name:
        raise AgentProvisionError("A name is required.")
    if not AGENT_NAME_PATTERN.match(name):
        raise AgentProvisionError("Agent name must be 2–24 characters, letters, spaces, and hyphens only.")
    if not description:
        raise AgentProvisionError("A description of the agent's expertise is required.")

    # Derive slug for technical identifiers (directories, Matrix username, config key)
    slug: str = name.lower().replace(" ", "-")

    agents: dict[str, Any] = config.get("agents") or {}
    if slug in agents:
        raise AgentProvisionError(f"An agent named '{name}' already exists.")

    display_name: str = name

    # Default model — same as the main agent
    if not model:
        model = config["main-agent"]["model"]
    if model not in (config.get("models") or {}):
        raise AgentProvisionError(f"Model '{model}' is not defined in the configuration.")

    homeserver: str = get_homeserver(config=config)
    server_name: str = get_server_name(config=config)

    if is_managed(config=config):
        # Managed (local Conduit): register a fresh Matrix user automatically
        username: str = slug
        password: str = generate_password()
        user_id: str = f"@{username}:{server_name}"

        try:
            result: dict[str, Any] = register_matrix_user(
                config=config,
                homeserver=homeserver,
                username=username,
                password=password,
            )
            access_token: str = result.get("access_token", "")
            user_id = result.get("user_id", user_id)
        except requests.exceptions.HTTPError as e:
            detail: str = e.response.text if e.response is not None else str(e)
            raise AgentProvisionError(f"Failed to register the Matrix account — {detail}")

        if not access_token:
            raise AgentProvisionError("Registration succeeded but the homeserver returned no access token.")
    else:
        # External homeserver: the operator must pre-create the Matrix account and
        # provide its credentials, since registration is typically not available.
        user_id = (matrix_user_id or "").strip()
        access_token = (matrix_access_token or "").strip()
        if not user_id or not access_token:
            raise AgentProvisionError(
                f"This deployment uses an external Matrix homeserver ({server_name}), which can't create "
                f"accounts automatically. Create a Matrix account for '{display_name}' (e.g. @{slug}:{server_name}) "
                f"on your homeserver, then supply its full user ID and an access token."
            )
        if not user_id.startswith("@") or ":" not in user_id:
            raise AgentProvisionError("The Matrix user ID must be a full ID like '@name:server'.")

    # Set display name (best-effort)
    try:
        set_display_name(
            homeserver=homeserver,
            user_id=user_id,
            access_token=access_token,
            display_name=f"{display_name} ⚡",
        )
    except Exception:
        logger.warning("Failed to set display name for agent '%s'", name)

    # Scaffold the workspace on disk
    workspace_dir: str = scaffold_workspace(
        config=config,
        name=slug,
        description=description,
        display_name=display_name,
    )

    agent_config: dict[str, Any] = {
        "description": description,
        "display_name": display_name,
        "model": model,
        "matrix_user_id": user_id,
        "matrix_access_token": access_token,
        "workspace": workspace_dir,
        "sessions": {},
    }
    return slug, agent_config

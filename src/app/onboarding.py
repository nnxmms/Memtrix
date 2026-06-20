#!/usr/bin/python3

import json
import os
import secrets
import string
import time
from typing import Any

import requests

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from src.core.config import CONFIG_PATH
from src.providers.utils import get_requirements
from src.integrations.bitwarden import BitwardenSecrets

# Rich console
console: Console = Console()


def _say(message: str) -> None:
    """
    This function prints a Memtrix speech bubble.
    """
    console.print()
    console.print(Panel(
        message,
        title="[bold cyan]Memtrix[/bold cyan]",
        title_align="left",
        border_style="cyan",
        padding=(0, 1)
    ))
    time.sleep(0.3)


def _generate_password(length: int = 24) -> str:
    """
    This function generates a random password.
    """
    alphabet: str = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class Onboarding:

    def __init__(self) -> None:
        """
        This is the Onboarding class which walks the user through initial setup.
        """
        with open(file=CONFIG_PATH, mode="r") as f:
            self.config: dict[str, Any] = json.load(fp=f)

        # Secrets collected during onboarding, printed as .env block at the end
        # Each entry is (env_var, value, description)
        self._env_secrets: list[tuple[str, str, str]] = []

        # Bitwarden secrets backend state (configured in _setup_secrets_backend)
        self._use_bitwarden: bool = False
        self._bitwarden: BitwardenSecrets | None = None
        self._bitwarden_token: str = ""

    def _save_config(self) -> None:
        """
        This function saves the current config to disk.
        """
        with open(file=CONFIG_PATH, mode="w") as f:
            json.dump(obj=self.config, fp=f, indent=4, default=str)

    def setup_new_provider(self) -> None:
        """
        This function is used to interactively setup a new provider.
        """
        # Get available providers
        providers: dict[str, list[str]] = get_requirements()
        provider_names: list[str] = list[str](providers.keys())

        _say(
            message=f"First things first — I need you to set up an [bold]LLM Provider[/bold].\n"
            f"A provider is the backend that hosts your models.\n\n"
            f"Currently we support the following providers:"
        )

        # Choose provider type using enforced selection
        selection: str = Prompt.ask(" [cyan]>[/cyan] Choose from", choices=provider_names)

        # Give this provider instance a name
        _say(message=f"Great choice! Now give this [bold]{selection}[/bold] instance a name so you can refer to it later.")
        instance_name: str = Prompt.ask(" [cyan]>[/cyan] Instance name", default=selection)

        # Collect required parameters for this provider
        _say(message=f"Almost there! I just need a few details to connect to [bold]{instance_name}[/bold].")
        params: dict[str, str] = {"type": selection}
        env_secrets: list[tuple[str, str]] = []
        for requirement in providers[selection]:
            value: str = Prompt.ask(f" [cyan]>[/cyan] {requirement}").strip()
            # Detect secret fields and store as $PLACEHOLDER
            if any(keyword in requirement.lower() for keyword in ("key", "token", "secret")):
                placeholder: str = f"{selection.upper()}_{requirement.upper()}"
                env_var: str = f"MEMTRIX_SECRET_{placeholder}"
                params[requirement] = f"${placeholder}"
                env_secrets.append((env_var, value, f"{selection.capitalize()} — {requirement}"))
            else:
                params[requirement] = value

        # Store provider instance in config
        self.config["providers"][instance_name] = params
        _say(message=f"[green]Provider [bold]{instance_name}[/bold] has been saved![/green]")

        # Collect secrets for the final .env block
        self._env_secrets.extend(env_secrets)

        # Offer to add another provider
        if Confirm.ask(" [cyan]>[/cyan] Want to add another provider?", default=False):
            self.setup_new_provider()

    def setup_new_model(self) -> None:
        """
        This function is used to interactively setup a new model instance.
        """
        provider_names: list[str] = list[str](self.config["providers"].keys())

        _say(
            message=f"Now let's configure a [bold]Model[/bold].\n"
            f"A model runs on one of your providers. Which provider should this model use?\n\n"
            f"Configured providers: [bold cyan]{', '.join(provider_names)}[/bold cyan]"
        )

        # Choose which configured provider this model belongs to using enforced selection
        provider: str = Prompt.ask(" [cyan]>[/cyan] Provider", choices=provider_names)

        # Model name on the provider (e.g. llama3, glm-4.7-flash:q8_0)
        _say(message=f"What's the model name as it appears on [bold]{provider}[/bold]? [dim](e.g. llama3, glm-4.7-flash:q8_0)[/dim]")
        model: str = Prompt.ask(" [cyan]>[/cyan] Model name").strip()
        if not model:
            _say(message="[bold red]Oops, model name can't be empty.[/bold red] Let's try that again.")
            return self.setup_new_model()

        # Give this model instance a name
        _say(message=f"Give this model a friendly instance name so you can refer to it later.")
        instance_name: str = Prompt.ask(" [cyan]>[/cyan] Instance name", default=model).strip()
        if not instance_name:
            _say(message="[bold red]Oops, instance name can't be empty.[/bold red] Let's try that again.")
            return self.setup_new_model()

        # Store model instance in config
        self.config["models"][instance_name] = {"provider": provider, "model": model}
        _say(message=f"[green]Model [bold]{instance_name}[/bold] has been saved![/green]")

        # Offer to add another model
        if Confirm.ask(" [cyan]>[/cyan] Want to add another model?", default=False):
            self.setup_new_model()

    def _register_matrix_user(self, homeserver: str, username: str, password: str) -> dict[str, Any]:
        """
        This function registers a user on the Matrix homeserver via the Client-Server API.
        Supports Conduit's registration_token for token-protected registration.
        """
        url: str = f"{homeserver.rstrip('/')}/_matrix/client/v3/register"
        body: dict[str, Any] = {
            "username": username,
            "password": password,
            "auth": {"type": "m.login.dummy"},
            "inhibit_login": False
        }

        # If a registration token is configured, include it in the auth flow
        reg_token: str = self._get_registration_token()
        if reg_token:
            body["auth"] = {
                "type": "m.login.registration_token",
                "token": reg_token
            }

        response: requests.Response = requests.post(url=url, json=body, timeout=30)
        response.raise_for_status()
        return response.json()

    def _get_registration_token(self) -> str:
        """
        This function reads the registration token from the Conduit config file.
        """
        conduit_toml_path: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "conduit.toml")
        if os.path.isfile(conduit_toml_path):
            with open(file=conduit_toml_path, mode="r") as f:
                for line in f:
                    if line.strip().startswith("registration_token"):
                        # Parse: registration_token = "value"
                        parts = line.split("=", maxsplit=1)
                        if len(parts) == 2:
                            token: str = parts[1].strip().strip('"').strip("'")
                            if token:
                                return token
        return ""

    def _set_display_name(self, homeserver: str, user_id: str, access_token: str, display_name: str) -> None:
        """
        This function sets a user's display name on the Matrix homeserver.
        """
        url: str = f"{homeserver.rstrip('/')}/_matrix/client/v3/profile/{requests.utils.quote(user_id)}/displayname"
        requests.put(
            url=url,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"displayname": display_name},
            timeout=10
        )

    def _setup_local_matrix(self) -> dict[str, Any]:
        """
        This function sets up a Matrix channel against the bundled local Conduit
        homeserver, registering the admin, bot, and user accounts automatically.
        Returns the channel params dict.
        """
        _say(
            message="Let's set up your [bold]local Matrix[/bold] channel!\n\n"
            "I'll create three accounts on your Conduit homeserver:\n"
            "  • [bold]admin[/bold] — the server administrator\n"
            "  • [bold]memtrix[/bold] — the bot account\n"
            "  • [bold]your user account[/bold] — so you can chat with Memtrix\n\n"
            "Make sure Conduit is running and registration is enabled."
        )

        # Inside Docker we reach Conduit via the service name
        homeserver: str = "http://conduit:6167"

        username: str = Prompt.ask(" [cyan]>[/cyan] Your username (e.g. alice)").strip()
        while not username:
            _say(message="[bold red]Username can't be empty.[/bold red] Let's try that again.")
            username = Prompt.ask(" [cyan]>[/cyan] Your username (e.g. alice)").strip()

        # Generate random passwords
        admin_password: str = _generate_password()
        bot_password: str = _generate_password()
        user_password: str = _generate_password()

        # Use the configured agent name for the bot account
        agent_name: str = self.config["main-agent"].get("name", "Memtrix")
        bot_username: str = agent_name.lower().replace(" ", "-")

        # Register the three accounts
        accounts: list[tuple[str, str, str]] = [
            ("admin", admin_password, "Admin"),
            (bot_username, bot_password, agent_name),
            (username, user_password, username.capitalize())
        ]

        _say(message="Registering accounts on the homeserver...")

        # Resolve server name for user IDs (matches the Conduit server_name)
        server_name: str = "memtrix.local"

        bot_data: dict[str, Any] = {}
        for acct_username, acct_password, acct_label in accounts:
            try:
                result: dict[str, Any] = self._register_matrix_user(
                    homeserver=homeserver,
                    username=acct_username,
                    password=acct_password
                )
                # Set the display name
                acct_user_id: str = result.get("user_id", f"@{acct_username}:{server_name}")
                acct_token: str = result.get("access_token", "")
                if acct_token:
                    self._set_display_name(
                        homeserver=homeserver,
                        user_id=acct_user_id,
                        access_token=acct_token,
                        display_name=acct_label
                    )
                console.print(f"  [green]✓[/green] {acct_label} ([bold]{acct_username}[/bold]) registered")
                if acct_username == bot_username:
                    bot_data = result
            except requests.exceptions.HTTPError as e:
                console.print(f"  [red]✗[/red] {acct_label} ([bold]{acct_username}[/bold]): {e.response.text}")

        # Print credentials table
        _say(message="Here are the accounts I created. [bold]Save these passwords now![/bold]")

        table: Table = Table(title="Matrix Accounts")
        table.add_column("Account", style="cyan")
        table.add_column("User ID", style="white")
        table.add_column("Password", style="green")
        for acct_username, acct_password, acct_label in accounts:
            table.add_row(acct_label, f"@{acct_username}:{server_name}", acct_password)
        console.print(table)

        # Use the bot's access token from registration
        bot_user_id: str = bot_data.get("user_id", f"@{bot_username}:{server_name}")
        bot_access_token: str = bot_data.get("access_token", "")

        if bot_access_token:
            _say(
                message=f"I got the bot access token automatically from registration.\n\n"
                f"Bot user ID: [bold]{bot_user_id}[/bold]"
            )
        else:
            _say(
                message=f"I couldn't get the bot access token automatically.\n"
                f"Please log in as [bold]{bot_username}[/bold] using Element Desktop and provide the access token."
            )
            bot_access_token = Prompt.ask(" [cyan]>[/cyan] Bot access token").strip()

        _say(
            message="Now log in with [bold]Element Desktop[/bold] using your account.\n\n"
            f"  Homeserver: [bold]http://localhost:6167[/bold]\n"
            f"  Username:   [bold]@{username}:{server_name}[/bold]\n"
            f"  Password:   [bold]{user_password}[/bold]\n\n"
            f"Then start a DM with [bold]@{bot_username}:{server_name}[/bold] to chat!"
        )

        # Collect the bot token for the final .env block
        self._env_secrets.append(("MEMTRIX_SECRET_MATRIX_ACCESS_TOKEN", bot_access_token, "Matrix bot access token for Conduit"))

        return {
            "type": "matrix",
            "homeserver": homeserver,
            "user_id": bot_user_id,
            "access_token": "$MATRIX_ACCESS_TOKEN",
            "display_name": f"{agent_name} ⚡",
            "managed": True
        }

    def _setup_external_matrix(self) -> dict[str, Any] | None:
        """
        This function sets up a Matrix channel against an external, already-hosted
        homeserver. The user supplies the homeserver URL, the bot's full user ID, and
        an access token for that account. Returns the channel params dict, or None if
        the details could not be verified (so onboarding can retry).
        """
        _say(
            message="Let's connect to your [bold]external Matrix homeserver[/bold]!\n\n"
            "You'll need a Matrix account for the bot already created on your server, "
            "plus an access token for it.\n\n"
            "Tip: log in as the bot account in Element, then find the access token under "
            "[italic]Settings → Help & About → Advanced[/italic]."
        )

        homeserver: str = Prompt.ask(
            " [cyan]>[/cyan] Homeserver URL (e.g. https://matrix.example.org)"
        ).strip().rstrip("/")
        if not homeserver:
            _say(message="[bold red]Homeserver URL can't be empty.[/bold red]")
            return None

        bot_user_id: str = Prompt.ask(
            " [cyan]>[/cyan] Bot user ID (e.g. @memtrix:example.org)"
        ).strip()
        if not bot_user_id.startswith("@") or ":" not in bot_user_id:
            _say(message="[bold red]That doesn't look like a Matrix user ID.[/bold red] It should be like @name:server.")
            return None

        bot_access_token: str = Prompt.ask(" [cyan]>[/cyan] Bot access token", password=True).strip()
        if not bot_access_token:
            _say(message="[bold red]Access token can't be empty.[/bold red]")
            return None

        # Verify the credentials by calling /whoami
        _say(message="Verifying the access token...")
        try:
            whoami_url: str = f"{homeserver}/_matrix/client/v3/account/whoami"
            response: requests.Response = requests.get(
                url=whoami_url,
                headers={"Authorization": f"Bearer {bot_access_token}"},
                timeout=15
            )
            response.raise_for_status()
            resolved_id: str = response.json().get("user_id", "")
        except requests.exceptions.RequestException as e:
            _say(message=f"[bold red]Couldn't reach the homeserver or the token is invalid:[/bold red] {e}")
            return None

        if resolved_id and resolved_id != bot_user_id:
            _say(
                message=f"[yellow]Heads up:[/yellow] the token belongs to [bold]{resolved_id}[/bold], "
                f"not [bold]{bot_user_id}[/bold]. Using [bold]{resolved_id}[/bold]."
            )
            bot_user_id = resolved_id

        console.print(f"  [green]✓[/green] Connected as [bold]{bot_user_id}[/bold]")

        agent_name: str = self.config["main-agent"].get("name", "Memtrix")
        server_name: str = bot_user_id.split(sep=":", maxsplit=1)[1]

        # Optionally set the bot's display name (best-effort)
        try:
            self._set_display_name(
                homeserver=homeserver,
                user_id=bot_user_id,
                access_token=bot_access_token,
                display_name=f"{agent_name} ⚡"
            )
        except Exception:
            pass

        _say(
            message=f"All set! Start a DM with [bold]{bot_user_id}[/bold] from your own "
            f"Matrix account on [bold]{server_name}[/bold] to chat.\n\n"
            f"[italic]Note:[/italic] on an external homeserver, sub-agents can't be created "
            f"automatically. You'll pre-create a Matrix account for each one and provide its token."
        )

        # Collect the bot token for the final .env block
        self._env_secrets.append(("MEMTRIX_SECRET_MATRIX_ACCESS_TOKEN", bot_access_token, "Matrix bot access token for external homeserver"))

        return {
            "type": "matrix",
            "homeserver": homeserver,
            "user_id": bot_user_id,
            "access_token": "$MATRIX_ACCESS_TOKEN",
            "display_name": f"{agent_name} ⚡",
            "managed": False
        }

    def setup_new_channel(self) -> None:
        """
        This function is used to interactively setup a new channel instance.
        """
        # Available channel types and their required parameters
        channel_types: dict[str, list[str]] = {
            "cli": [],
            "matrix": ["homeserver", "user_id", "access_token"]
        }
        type_names: list[str] = list[str](channel_types.keys())

        _say(
            message=f"Now let's set up a [bold]Channel[/bold].\n"
            f"A channel is how you communicate with Memtrix.\n\n"
            f"Available types: [bold cyan]{', '.join(type_names)}[/bold cyan]"
        )

        # Choose channel type
        selection: str = Prompt.ask(" [cyan]>[/cyan] Channel type", choices=type_names)

        # Give this channel instance a name
        _say(message=f"Give this [bold]{selection}[/bold] channel a name so you can refer to it later.")
        instance_name: str = Prompt.ask(" [cyan]>[/cyan] Instance name", default=selection).strip()
        if not instance_name:
            _say(message="[bold red]Oops, instance name can't be empty.[/bold red] Let's try that again.")
            return self.setup_new_channel()

        params: dict[str, Any] = {"type": selection}

        if selection == "matrix":
            # Choose between the bundled local Conduit homeserver and an external one
            _say(
                message="Where is your [bold]Matrix homeserver[/bold]?\n\n"
                "  • [bold]local[/bold] — the Conduit homeserver bundled with Memtrix (recommended)\n"
                "  • [bold]external[/bold] — a server you already host (e.g. your own Synapse, matrix.org)"
            )
            location: str = Prompt.ask(
                " [cyan]>[/cyan] Homeserver",
                choices=["local", "external"],
                default="local"
            )

            if location == "external":
                external: dict[str, Any] | None = self._setup_external_matrix()
                if external is None:
                    return self.setup_new_channel()
                params = external
            else:
                params = self._setup_local_matrix()

        elif channel_types[selection]:
            _say(message=f"I need a few details to set up [bold]{instance_name}[/bold].")
            for requirement in channel_types[selection]:
                value: str = Prompt.ask(f" [cyan]>[/cyan] {requirement}").strip()
                params[requirement] = value

        # Store channel instance in config
        self.config["channels"][instance_name] = params
        _say(message=f"[green]Channel [bold]{instance_name}[/bold] has been saved![/green]")

        # Offer to add another channel
        if Confirm.ask(" [cyan]>[/cyan] Want to add another channel?", default=False):
            self.setup_new_channel()

    def setup_main_agent(self) -> None:
        """
        This function asks the user to select the model and channel for the main agent.
        """
        model_names: list[str] = list[str](self.config["models"].keys())
        channel_names: list[str] = list[str](self.config["channels"].keys())

        _say(
            message=f"Almost done! Which [bold]model[/bold] should your main agent use?\n\n"
            f"Configured models: [bold cyan]{', '.join(model_names)}[/bold cyan]"
        )

        # Choose model
        model: str = Prompt.ask(
            " [cyan]>[/cyan] Main agent model",
            choices=model_names,
            default=model_names[0]
        )

        self.config["main-agent"]["model"] = model
        self.config["main-agent"]["provider"] = self.config["models"][model]["provider"]

        _say(
            message=f"And which [bold]channel[/bold] should the main agent use?\n\n"
            f"Configured channels: [bold cyan]{', '.join(channel_names)}[/bold cyan]"
        )

        # Choose channel
        channel: str = Prompt.ask(
            " [cyan]>[/cyan] Main agent channel",
            choices=channel_names,
            default=channel_names[0]
        )

        self.config["main-agent"]["channel"] = channel

    def _setup_secrets_backend(self) -> None:
        """
        This function optionally configures Bitwarden Secrets Manager as the
        secrets backend. When enabled, all collected secrets are stored in
        Bitwarden at the end of onboarding and only the access token is written
        to the .env file.
        """
        _say(
            message="Where should I keep your [bold]secrets[/bold] (API keys, Matrix tokens)?\n\n"
            "By default they're written to a local [bold].env[/bold] file.\n"
            "Alternatively, I can use [bold]Bitwarden Secrets Manager[/bold] — then the only "
            "secret on this machine is a single Bitwarden access token, and everything else "
            "is fetched from Bitwarden at startup."
        )

        if not Confirm.ask(" [cyan]>[/cyan] Use Bitwarden Secrets Manager?", default=False):
            return

        while True:
            _say(
                message="Great! I need a [bold]machine account access token[/bold] with "
                "[bold]read-write[/bold] access so I can store your secrets.\n"
                "You can create one in the Bitwarden Secrets Manager under "
                "[dim]Machine accounts → Access tokens[/dim]."
            )
            token: str = Prompt.ask(" [cyan]>[/cyan] Bitwarden access token", password=True).strip()
            if not token:
                _say(message="[bold red]Token can't be empty.[/bold red] Let's try again.")
                continue

            # Self-hosted support: optional custom endpoints (needed before we connect)
            api_url: str | None = None
            identity_url: str | None = None
            if Confirm.ask(" [cyan]>[/cyan] Using a self-hosted Bitwarden server?", default=False):
                api_url = Prompt.ask(" [cyan]>[/cyan] API URL", default="https://api.bitwarden.eu").strip() or None
                identity_url = Prompt.ask(" [cyan]>[/cyan] Identity URL", default="https://identity.bitwarden.eu").strip() or None

            # Authenticate with the access token (no org ID required yet)
            client: BitwardenSecrets = BitwardenSecrets(
                api_url=api_url,
                identity_url=identity_url,
            )
            try:
                client.connect(access_token=token)
            except Exception as exc:
                _say(message=f"[bold red]Authentication failed:[/bold red] {exc}\n\nLet's try again.")
                if not Confirm.ask(" [cyan]>[/cyan] Retry Bitwarden setup?", default=True):
                    _say(message="[yellow]Skipping Bitwarden — secrets will be written to .env instead.[/yellow]")
                    return
                continue

            # Resolve the organization: auto-detect from the token if possible, else ask
            organization_id: str | None = client.detect_organization_id()
            if organization_id:
                _say(message=f"Detected organization [bold]{organization_id}[/bold] from your access token.")
            while not organization_id:
                _say(
                    message="A Secrets Manager access token is tied to one [bold]organization[/bold], "
                    "but I couldn't read its ID automatically.\n"
                    "You'll find it in the Bitwarden web vault URL or under "
                    "[dim]Organization settings → Information[/dim]."
                )
                organization_id = Prompt.ask(" [cyan]>[/cyan] Organization ID").strip() or None
            client.set_organization_id(organization_id=organization_id)

            # Verify the token can actually reach this organization's secrets
            if not client.test_connection():
                _say(
                    message="[bold red]Couldn't access that organization's secrets.[/bold red] "
                    "Double-check the organization ID and that the token has read-write access."
                )
                if not Confirm.ask(" [cyan]>[/cyan] Retry Bitwarden setup?", default=True):
                    _say(message="[yellow]Skipping Bitwarden — secrets will be written to .env instead.[/yellow]")
                    return
                continue

            # Choose a project to store secrets in (selection is required)
            try:
                projects: list[tuple[str, str]] = client.list_projects()
            except Exception as exc:
                _say(message=f"[bold red]Couldn't list projects:[/bold red] {exc}")
                projects = []

            if not projects:
                _say(
                    message="[bold red]No projects found in this organization.[/bold red]\n"
                    "Create a project in Bitwarden Secrets Manager (and grant this machine "
                    "account access to it), then come back."
                )
                if not Confirm.ask(" [cyan]>[/cyan] Retry Bitwarden setup?", default=True):
                    _say(message="[yellow]Skipping Bitwarden — secrets will be written to .env instead.[/yellow]")
                    return
                continue

            _say(message="Which [bold]project[/bold] should I store your secrets in?")
            table: Table = Table(show_header=True, header_style="bold cyan")
            table.add_column("#")
            table.add_column("Project")
            for idx, (_, name) in enumerate(projects, start=1):
                table.add_row(str(idx), name)
            console.print(table)
            choice: str = Prompt.ask(
                " [cyan]>[/cyan] Project",
                choices=[str(i) for i in range(1, len(projects) + 1)],
            )
            project_id: str = projects[int(choice) - 1][0]
            project_name: str = projects[int(choice) - 1][1]
            client.set_project_id(project_id=project_id)
            _say(message=f"Using Bitwarden project [bold]{project_name}[/bold].")

            # Persist non-secret backend configuration
            self.config["secrets"] = {
                "backend": "bitwarden",
                "organization_id": organization_id,
                "project_id": project_id,
                "api_url": api_url,
                "identity_url": identity_url,
            }
            self._use_bitwarden = True
            self._bitwarden = client
            self._bitwarden_token = token
            _say(message="[green]Bitwarden Secrets Manager is configured![/green]")
            return

    def _setup_name(self) -> None:
        """
        This function asks the user to pick a name for the main agent.
        """
        _say(
            message="First — what should I call myself?\n\n"
            "This will be my name everywhere: Matrix display name, how I introduce myself, etc.\n"
            "Leave blank to keep the default: [bold]Memtrix[/bold]."
        )
        name: str = Prompt.ask(" [cyan]>[/cyan] Agent name", default="Memtrix").strip()
        if not name:
            name = "Memtrix"
        self.config["main-agent"]["name"] = name
        _say(message=f"Got it — I'm [bold]{name}[/bold] from now on.")

    def run(self) -> None:
        """
        This function runs the onboarding wizard.
        """
        _say(
            message="Hey there! :wave: Welcome to [bold]Memtrix[/bold]!\n\n"
            "I'm going to walk you through a quick setup so we can get you up and running.\n"
            "This should only take a minute."
        )

        # Ask for a name for the main agent
        self._setup_name()

        # Choose a secrets backend (env file or Bitwarden Secrets Manager)
        self._setup_secrets_backend()

        # Setup model providers
        self.setup_new_provider()

        # Setup model instances
        self.setup_new_model()

        # Setup channels
        self.setup_new_channel()

        # Setup main agent
        self.setup_main_agent()

        # Persist config
        self._save_config()

        # Update workspace AGENT.md with the chosen name
        agent_name: str = self.config["main-agent"].get("name", "Memtrix")
        workspace_agent_md: str = os.path.join(self.config["workspace-directory"], "AGENT.md")
        if agent_name != "Memtrix" and os.path.isfile(workspace_agent_md):
            with open(file=workspace_agent_md, mode="r") as f:
                content: str = f.read()
            content = content.replace(
                "You are **Memtrix**, a personal AI assistant.",
                f"You are **{agent_name}**, a personal AI assistant."
            )
            with open(file=workspace_agent_md, mode="w") as f:
                f.write(content)

        # Print .env block with all collected secrets
        if self._use_bitwarden and self._bitwarden is not None:
            # Store every collected secret in Bitwarden, keyed by the placeholder
            # name (the env var without the MEMTRIX_SECRET_ prefix).
            created: list[str] = []
            failed: list[tuple[str, str]] = []
            for key, value, description in self._env_secrets:
                bw_key: str = key[len("MEMTRIX_SECRET_"):] if key.startswith("MEMTRIX_SECRET_") else key
                try:
                    self._bitwarden.create_secret(key=bw_key, value=value, note=description)
                    created.append(bw_key)
                except Exception as exc:
                    failed.append((bw_key, str(exc)))

            # Only the Bitwarden access token is written to .env
            env_content: str = (
                "# Memtrix secrets — generated by onboarding\n\n"
                "# Bitwarden Secrets Manager access token\n"
                f"BWS_ACCESS_TOKEN={self._bitwarden_token}\n"
            )
            env_path: str = os.path.join(os.path.dirname(CONFIG_PATH), ".env.generated")
            with open(file=env_path, mode="w") as f:
                f.write(env_content)

            if created:
                table: Table = Table(show_header=True, header_style="bold cyan", title="Secrets stored in Bitwarden")
                table.add_column("Secret key")
                for bw_key in created:
                    table.add_row(bw_key)
                console.print(table)

            if failed:
                fail_lines: str = "\n".join(f"  • {k}: {e}" for k, e in failed)
                _say(
                    message="[bold red]Some secrets could not be stored in Bitwarden:[/bold red]\n"
                    f"{fail_lines}\n\nPlease add them manually before starting Memtrix."
                )

            _say(
                message="[bold green]All done![/bold green] :sparkles: Your configuration has been saved.\n\n"
                "Your secrets are stored in [bold]Bitwarden[/bold]. Only the access token "
                "lives in your [bold].env[/bold] file, which will be placed in the project root automatically.\n\n"
                "Start Memtrix by running:\n\n"
                "  [bold]docker compose up[/bold]"
            )
        elif self._env_secrets:
            env_lines: list[str] = ["# Memtrix secrets — generated by onboarding", ""]
            for key, value, description in self._env_secrets:
                env_lines.append(f"# {description}")
                env_lines.append(f"{key}={value}")
                env_lines.append("")
            env_content: str = "\n".join(env_lines)

            # Write to data directory (mounted volume) so the user can copy it
            env_path: str = os.path.join(os.path.dirname(CONFIG_PATH), ".env.generated")
            with open(file=env_path, mode="w") as f:
                f.write(env_content)

            _say(
                message="[bold green]All done![/bold green] :sparkles: Your configuration has been saved.\n\n"
                "Your [bold].env[/bold] file will be placed in the project root automatically.\n\n"
                "Start Memtrix by running:\n\n"
                "  [bold]docker compose up[/bold]"
            )
        else:
            _say(
                message="[bold green]All done![/bold green] :sparkles: Your configuration has been saved.\n\n"
                "You can now start Memtrix by running:\n\n"
                "  [bold]docker compose up[/bold]"
            )


if __name__ == "__main__":
    # Run the onboarding wizard
    wizard: Onboarding = Onboarding()
    wizard.run()

#!/usr/bin/python3

import json
import secrets
import string
import time
from typing import Any

import requests

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from src.config import CONFIG_PATH
from src.providers.utils import get_requirements

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

    def _save_config(self) -> None:
        """
        This function saves the current config to disk.
        """
        with open(file=CONFIG_PATH, mode="w") as f:
            json.dump(obj=self.config, fp=f, indent=4)

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
        for requirement in providers[selection]:
            value: str = Prompt.ask(f" [cyan]>[/cyan] {requirement}").strip()
            params[requirement] = value

        # Store provider instance in config
        self.config["providers"][instance_name] = params
        _say(message=f"[green]Provider [bold]{instance_name}[/bold] has been saved![/green]")

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
        """
        url: str = f"{homeserver.rstrip('/')}/_matrix/client/v3/register"
        response: requests.Response = requests.post(
            url=url,
            json={
                "username": username,
                "password": password,
                "auth": {"type": "m.login.dummy"},
                "inhibit_login": False
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()

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

        params: dict[str, str] = {"type": selection}

        if selection == "matrix":
            # Matrix-specific onboarding: create 3 users on Conduit
            _say(
                message="Let's set up your [bold]Matrix[/bold] channel!\n\n"
                "I'll create three accounts on your Conduit homeserver:\n"
                "  • [bold]admin[/bold] — the server administrator\n"
                "  • [bold]memtrix[/bold] — the bot account\n"
                "  • [bold]your user account[/bold] — so you can chat with Memtrix\n\n"
                "Make sure Conduit is running and registration is enabled."
            )

            # Inside Docker we reach Conduit via the service name
            homeserver: str = "http://conduit:6167"

            username: str = Prompt.ask(" [cyan]>[/cyan] Your username (e.g. alice)").strip()
            if not username:
                _say(message="[bold red]Username can't be empty.[/bold red] Let's try that again.")
                return self.setup_new_channel()

            # Generate random passwords
            admin_password: str = _generate_password()
            bot_password: str = _generate_password()
            user_password: str = _generate_password()

            # Register the three accounts
            accounts: list[tuple[str, str, str]] = [
                ("admin", admin_password, "Admin"),
                ("memtrix", bot_password, "Memtrix"),
                (username, user_password, username.capitalize())
            ]

            _say(message="Registering accounts on the homeserver...")

            # Resolve server name for user IDs (matches CONDUIT_SERVER_NAME)
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
                    if acct_username == "memtrix":
                        bot_data: dict[str, Any] = result
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
            bot_user_id: str = bot_data.get("user_id", f"@memtrix:{server_name}")
            bot_access_token: str = bot_data.get("access_token", "")

            if bot_access_token:
                _say(
                    message=f"I got the bot access token automatically from registration.\n\n"
                    f"Bot user ID: [bold]{bot_user_id}[/bold]"
                )
            else:
                _say(
                    message="I couldn't get the bot access token automatically.\n"
                    "Please log in as [bold]memtrix[/bold] using Element Desktop and provide the access token."
                )
                bot_access_token: Any | str = Prompt.ask(" [cyan]>[/cyan] Bot access token").strip()

            _say(
                message="Now log in with [bold]Element Desktop[/bold] using your account.\n\n"
                f"  Homeserver: [bold]http://localhost:6167[/bold]\n"
                f"  Username:   [bold]@{username}:{server_name}[/bold]\n"
                f"  Password:   [bold]{user_password}[/bold]\n\n"
                "Then start a DM with [bold]@memtrix:memtrix.local[/bold] to chat!"
            )

            params["homeserver"] = homeserver
            params["user_id"] = bot_user_id
            params["access_token"] = "$MATRIX_ACCESS_TOKEN"

            # Tell the user to add the token to .env
            _say(
                message="[bold yellow]Important:[/bold yellow] Add the bot access token to your [bold].env[/bold] file "
                "in the Memtrix project root:\n\n"
                f"  [bold]MEMTRIX_SECRET_MATRIX_ACCESS_TOKEN={bot_access_token}[/bold]\n\n"
                "Memtrix will read it from there on startup. Never put tokens in config.json."
            )

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

    def run(self) -> None:
        """
        This function runs the onboarding wizard.
        """
        _say(
            message="Hey there! :wave: Welcome to [bold]Memtrix[/bold]!\n\n"
            "I'm going to walk you through a quick setup so we can get you up and running.\n"
            "This should only take a minute."
        )

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
        _say(
            message="[bold green]All done![/bold green] :sparkles: Your configuration has been saved.\n\n"
            "You can now start Memtrix by running:\n\n"
            "  [bold]docker compose up[/bold]"
        )


if __name__ == "__main__":
    # Run the onboarding wizard
    wizard: Onboarding = Onboarding()
    wizard.run()

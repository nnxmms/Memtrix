#!/usr/bin/python3

from typing import Callable

from src.channels.base import BaseChannel


class CLIChannel(BaseChannel):

    def __init__(self) -> None:
        """
        This is the CLIChannel class which provides a CLI interface.
        """
        super().__init__(name="cli")

    def send_message(self, message: str) -> None:
        """
        This function sends a message to stdout.
        """
        print(f"Memtrix: {message}")

    def send_to_room(self, room_id: str, body: str, notice: bool = False) -> None:
        """
        This function delivers an unsolicited message (e.g. a background worker
        result) to the CLI. The room id is ignored since the CLI is single-room.
        """
        _ = room_id
        prefix: str = "  " if notice else "Memtrix: "
        print(f"{prefix}{body}")

    def receive_message(self) -> str:
        """
        This function receives a message from stdin.
        """
        return input("You: ").strip()

    def run(self, handler: Callable[[str, str, Callable[[str], None]], str]) -> None:
        """
        This function starts the CLI loop, calling handler for each user message.
        """
        print("Memtrix CLI — type 'exit' to quit.")
        while True:
            # Read next user input
            user_input: str = self.receive_message()
            if user_input.lower() in ("exit", "quit"):
                break
            if not user_input:
                continue

            def ask(msg: str) -> str:
                print(f"  {msg}")
                return input("  > ").strip()

            # Notify prints status messages inline
            prefixed: str = f"[Channel: CLI, Sender: User]\n{user_input}"
            reply: str = handler(prefixed, "cli", lambda msg: print(f"  {msg}"), None, ask)
            self.send_message(message=reply)
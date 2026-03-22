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
            reply: str = handler(user_input, "cli", lambda msg: print(f"  {msg}"), None, ask)
            self.send_message(message=reply)
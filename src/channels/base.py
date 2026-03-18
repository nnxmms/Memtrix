#!/usr/bin/python3

from typing import Callable


class BaseChannel:

    def __init__(self, name: str) -> None:
        """
        This is the BaseChannel class which all channels inherit from.
        """
        # Channel name
        self.name: str = name

    def send_message(self, message: str) -> None:
        """
        This function sends a message over the channel.
        """
        raise NotImplementedError

    def receive_message(self) -> str:
        """
        This function receives a message from the channel.
        """
        raise NotImplementedError

    def run(self, handler: Callable[[str, str, Callable[[str], None]], str]) -> None:
        """
        This function starts the channel loop, calling handler for each incoming message.
        The handler receives the message, a room id, and a notify callback for status updates.
        """
        raise NotImplementedError
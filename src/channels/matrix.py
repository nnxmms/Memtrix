#!/usr/bin/python3

import asyncio
import time
from urllib.parse import quote

from typing import Any, Callable

import aiohttp

from nio import AsyncClient, MatrixRoom, RoomMessageText, SyncResponse


class MatrixChannel:

    def __init__(self, homeserver: str, user_id: str, access_token: str) -> None:
        """
        This is the MatrixChannel class which provides a Matrix bot interface.
        """
        # Channel name
        self.name: str = "matrix"

        # Matrix connection details
        self._homeserver: str = homeserver.rstrip("/")
        self._user_id: str = user_id
        self._access_token: str = access_token

        # Handler callback
        self._handler: Callable[[str], str] | None = None

        # Async Matrix client
        self._client: AsyncClient | None = None

        # Timestamp after which we process messages
        self._start_time: float = 0.0

    async def _join_room(self, room_id: str) -> None:
        """
        This function joins a room via the Matrix REST API.
        Uses raw HTTP because nio's join() sends a body Conduit rejects.
        """
        url: str = f"{self._homeserver}/_matrix/client/v3/join/{quote(room_id)}"
        headers: dict[str, str] = {"Authorization": f"Bearer {self._access_token}"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url=url, headers=headers, json={}) as resp:
                print(f"Join {room_id}: {resp.status}")

    async def _on_message(self, room: MatrixRoom, event: RoomMessageText) -> None:
        """
        This function handles incoming Matrix room messages.
        """
        # Ignore messages from the bot itself
        if event.sender == self._user_id:
            return

        # Ignore messages from before the bot started
        if event.server_timestamp < self._start_time:
            return

        # Process the message and send the reply
        reply: str = self._handler(event.body)
        await self._client.room_send(
            room_id=room.room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": reply}
        )

    async def _on_sync(self, response: SyncResponse) -> None:
        """
        This function auto-joins rooms when the bot is invited.
        """
        for room_id in response.rooms.invite:
            await self._join_room(room_id=room_id)

    async def _run_async(self) -> None:
        """
        This function runs the async Matrix client loop.
        """
        self._client = AsyncClient(homeserver=self._homeserver, user=self._user_id)
        self._client.access_token = self._access_token

        # Register event and sync callbacks
        self._client.add_event_callback(self._on_message, RoomMessageText)
        self._client.add_response_callback(self._on_sync, SyncResponse)

        # Record start time (milliseconds) to skip old messages
        self._start_time = time.time() * 1000

        # Initial sync to catch up on pending invites
        print(f"Matrix channel connecting to {self._homeserver} as {self._user_id}...")
        response: Any = await self._client.sync(timeout=10000)

        # Join any rooms we were invited to while offline
        for room_id in response.rooms.invite:
            await self._join_room(room_id=room_id)

        print("Matrix channel ready. Listening for messages...")

        # Listen for new events indefinitely
        await self._client.sync_forever(timeout=30000)

    def run(self, handler: Callable[[str], str]) -> None:
        """
        This function starts the Matrix event loop, calling handler for each incoming message.
        """
        self._handler = handler
        asyncio.run(main=self._run_async())

#!/usr/bin/python3

import asyncio
import os
import time
from urllib.parse import quote

from typing import Any, Callable

import aiohttp

from nio import AsyncClient, MatrixRoom, RoomMessageText, RoomMessageFile, RoomMessageImage, RoomMessageAudio, RoomMessageVideo, SyncResponse


class MatrixChannel:

    def __init__(self, homeserver: str, user_id: str, access_token: str, display_name: str = "Memtrix", attachments_dir: str = "") -> None:
        """
        This is the MatrixChannel class which provides a Matrix bot interface.
        """
        # Channel name
        self.name: str = "matrix"

        # Matrix connection details
        self._homeserver: str = homeserver.rstrip("/")
        self._user_id: str = user_id
        self._access_token: str = access_token
        self._display_name: str = display_name

        # Attachments directory
        self._attachments_dir: str = attachments_dir
        if self._attachments_dir:
            os.makedirs(name=self._attachments_dir, exist_ok=True)

        # Handler callback
        self._handler: Callable[[str, str, Callable[[str], None], Callable[[str], None]], str] | None = None

        # Async Matrix client
        self._client: AsyncClient | None = None

        # Timestamp after which we process messages
        self._start_time: float = 0.0

        # Pending ask queues for human-in-the-loop confirmations (keyed by room_id)
        self._pending_asks: dict[str, asyncio.Queue[str]] = {}

        # Background handler tasks (prevent GC)
        self._tasks: set[asyncio.Task] = set()

    async def _set_display_name(self) -> None:
        """
        This function sets the bot's display name via the Matrix REST API.
        """
        url: str = f"{self._homeserver}/_matrix/client/v3/profile/{quote(string=self._user_id)}/displayname"
        headers: dict[str, str] = {"Authorization": f"Bearer {self._access_token}"}
        async with aiohttp.ClientSession() as session:
            async with session.put(url=url, headers=headers, json={"displayname": self._display_name}) as resp:
                if resp.status == 200:
                    print(f"Display name set to '{self._display_name}'")

    async def _join_room(self, room_id: str) -> None:
        """
        This function joins a room via the Matrix REST API.
        Uses raw HTTP because nio's join() sends a body Conduit rejects.
        """
        url: str = f"{self._homeserver}/_matrix/client/v3/join/{quote(string=room_id)}"
        headers: dict[str, str] = {"Authorization": f"Bearer {self._access_token}"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url=url, headers=headers, json={}) as resp:
                print(f"Join {room_id}: {resp.status}")

    async def _download_mxc(self, mxc_url: str, filename: str) -> str:
        """
        This function downloads a file from a Matrix mxc:// URL and saves it to the attachments directory.
        Returns the local file path.
        """
        # Sanitize filename to prevent path traversal (event.body is attacker-controlled)
        filename = os.path.basename(filename) or "attachment"

        # Parse mxc://server/media_id
        parts: str = mxc_url.replace("mxc://", "").split(sep="/", maxsplit=1)
        server_name: str = parts[0]
        media_id: str = parts[1] if len(parts) > 1 else ""

        url: str = f"{self._homeserver}/_matrix/client/v1/media/download/{server_name}/{media_id}"
        headers: dict[str, str] = {"Authorization": f"Bearer {self._access_token}"}

        filepath: str = os.path.join(self._attachments_dir, filename)

        # Handle filename collision — don't silently overwrite existing files
        if os.path.exists(path=filepath):
            base, ext = os.path.splitext(filename)
            counter: int = 1
            while os.path.exists(path=filepath):
                filename: str = f"{base}_{counter}{ext}"
                filepath: str = os.path.join(self._attachments_dir, filename)
                counter += 1

        async with aiohttp.ClientSession() as session:
            async with session.get(url=url, headers=headers) as resp:
                if resp.status == 200:
                    with open(file=filepath, mode="wb") as f:
                        f.write(await resp.read())
                    return filepath
        return ""

    async def _send_file_to_room(self, room_id: str, filepath: str) -> None:
        """
        This function uploads a file to Matrix and sends it to a room.
        """
        filename: str = os.path.basename(filepath)
        filesize: int = os.path.getsize(filename=filepath)

        # Upload via REST API
        with open(file=filepath, mode="rb") as f:
            file_data: bytes = f.read()

        url: str = f"{self._homeserver}/_matrix/media/v3/upload?filename={quote(string=filename)}"
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/octet-stream"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url=url, headers=headers, data=file_data) as resp:
                if resp.status != 200:
                    return
                result: dict[str, Any] = await resp.json()
                mxc_url: str = result.get("content_uri", "")

        if not mxc_url:
            return

        # Send file message
        await self._client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.file",
                "body": filename,
                "url": mxc_url,
                "info": {"size": filesize}
            }
        )

    async def _on_file(self, room: MatrixRoom, event: Any) -> None:
        """
        This function handles incoming file messages (m.file, m.image, m.audio, m.video).
        Downloads the file and passes a text message to the handler.
        """
        if event.sender == self._user_id:
            return
        if event.server_timestamp < self._start_time:
            return
        if not self._attachments_dir:
            return

        # Get file info from the event
        mxc_url: str = event.url or ""
        filename: str = event.body or "attachment"

        if not mxc_url:
            return

        # Download the file
        filepath: str = await self._download_mxc(mxc_url=mxc_url, filename=filename)
        if not filepath:
            return

        # Build a text message telling the agent about the file (use actual saved filename)
        saved_filename: str = os.path.basename(filepath)
        user_message: str = f"[File received: attachments/{saved_filename}]"

        loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()

        def notify(msg: str) -> None:
            future = asyncio.run_coroutine_threadsafe(
                self._client.room_send(
                    room_id=room.room_id,
                    message_type="m.room.message",
                    content={"msgtype": "m.notice", "body": msg}
                ),
                loop
            )
            future.result(timeout=10)

        def send_file(path: str) -> None:
            future = asyncio.run_coroutine_threadsafe(
                self._send_file_to_room(room_id=room.room_id, filepath=path),
                loop
            )
            future.result(timeout=30)

        def ask(msg: str) -> str:
            queue: asyncio.Queue[str] = asyncio.Queue()
            self._pending_asks[room.room_id] = queue
            try:
                send_future = asyncio.run_coroutine_threadsafe(
                    self._client.room_send(
                        room_id=room.room_id,
                        message_type="m.room.message",
                        content={"msgtype": "m.notice", "body": msg}
                    ),
                    loop
                )
                send_future.result(timeout=10)
                get_future = asyncio.run_coroutine_threadsafe(queue.get(), loop)
                return get_future.result(timeout=120)
            finally:
                self._pending_asks.pop(room.room_id, None)

        async def _process() -> None:
            try:
                reply: str = await asyncio.to_thread(self._handler, user_message, room.room_id, notify, send_file, ask)
                await self._client.room_send(
                    room_id=room.room_id,
                    message_type="m.room.message",
                    content={"msgtype": "m.text", "body": reply}
                )
            except Exception as e:
                print(f"Error processing file message: {e}")

        task: asyncio.Task = asyncio.create_task(_process())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

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

        # Route response to pending human-in-the-loop question
        if room.room_id in self._pending_asks:
            await self._pending_asks[room.room_id].put(item=event.body)
            return

        # Build a notify callback that sends messages to this room in real-time
        loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()

        def notify(msg: str) -> None:
            """
            Send a status message to the room synchronously from within the async loop.
            """
            future = asyncio.run_coroutine_threadsafe(
                self._client.room_send(
                    room_id=room.room_id,
                    message_type="m.room.message",
                    content={"msgtype": "m.notice", "body": msg}
                ),
                loop
            )
            future.result(timeout=10)

        def send_file(path: str) -> None:
            """
            Send a file to the room synchronously from within the async loop.
            """
            future = asyncio.run_coroutine_threadsafe(
                self._send_file_to_room(room_id=room.room_id, filepath=path),
                loop
            )
            future.result(timeout=30)

        def ask(msg: str) -> str:
            """
            Send a question to the room and wait for the user's response.
            """
            queue: asyncio.Queue[str] = asyncio.Queue()
            self._pending_asks[room.room_id] = queue
            try:
                send_future = asyncio.run_coroutine_threadsafe(
                    self._client.room_send(
                        room_id=room.room_id,
                        message_type="m.room.message",
                        content={"msgtype": "m.notice", "body": msg}
                    ),
                    loop
                )
                send_future.result(timeout=10)
                get_future = asyncio.run_coroutine_threadsafe(queue.get(), loop)
                return get_future.result(timeout=120)
            finally:
                self._pending_asks.pop(room.room_id, None)

        # Process the message as a background task so sync_forever can continue
        async def _process() -> None:
            try:
                reply: str = await asyncio.to_thread(self._handler, event.body, room.room_id, notify, send_file, ask)
                await self._client.room_send(
                    room_id=room.room_id,
                    message_type="m.room.message",
                    content={"msgtype": "m.text", "body": reply}
                )
            except Exception as e:
                print(f"Error processing message: {e}")

        task: asyncio.Task = asyncio.create_task(_process())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

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
        self._client.add_event_callback(self._on_file, RoomMessageFile)
        self._client.add_event_callback(self._on_file, RoomMessageImage)
        self._client.add_event_callback(self._on_file, RoomMessageAudio)
        self._client.add_event_callback(self._on_file, RoomMessageVideo)
        self._client.add_response_callback(self._on_sync, SyncResponse)

        # Record start time (milliseconds) to skip old messages
        self._start_time = time.time() * 1000

        # Set the display name
        await self._set_display_name()

        # Initial sync to catch up on pending invites
        print(f"Matrix channel connecting to {self._homeserver} as {self._user_id}...")
        response: Any = await self._client.sync(timeout=10000)

        # Join any rooms we were invited to while offline
        for room_id in response.rooms.invite:
            await self._join_room(room_id=room_id)

        print("Matrix channel ready. Listening for messages...")

        # Listen for new events indefinitely
        await self._client.sync_forever(timeout=30000)

    def run(self, handler: Callable[[str, str, Callable[[str], None], Callable[[str], None]], str]) -> None:
        """
        This function starts the Matrix event loop, calling handler for each incoming message.
        """
        self._handler = handler
        asyncio.run(main=self._run_async())

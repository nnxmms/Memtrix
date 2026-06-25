#!/usr/bin/python3

import asyncio
import logging
import mimetypes
import os
import time
from urllib.parse import quote

from typing import Any, Callable

import aiohttp

from nio import AsyncClient, MatrixRoom, RoomMessageText, RoomMessageFile, RoomMessageImage, RoomMessageAudio, RoomMessageVideo, SyncResponse

logger: logging.Logger = logging.getLogger(__name__)


class MatrixChannel:

    def __init__(self, homeserver: str, user_id: str, access_token: str, display_name: str = "Memtrix", attachments_dir: str = "", bot_user_ids: set[str] | None = None, voice_config: dict[str, Any] | None = None, transcriber: Any = None) -> None:
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

        # All known bot user IDs (used to ignore messages from other agents)
        self._bot_user_ids: set[str] = bot_user_ids or set()

        # Attachments directory
        self._attachments_dir: str = attachments_dir
        if self._attachments_dir:
            os.makedirs(name=self._attachments_dir, exist_ok=True)

        # Handler callback
        self._handler: Callable[[str, str, Callable[[str], None], Callable[[str], None]], str] | None = None

        # Async Matrix client
        self._client: AsyncClient | None = None

        # The running asyncio loop (captured in _run_async) so other threads can
        # schedule sends (e.g. background worker-result delivery) thread-safely.
        self._loop: asyncio.AbstractEventLoop | None = None

        # Timestamp after which we process messages
        self._start_time: float = 0.0

        # Pending ask queues for human-in-the-loop confirmations (keyed by room_id)
        self._pending_asks: dict[str, asyncio.Queue[str]] = {}

        # Background handler tasks (prevent GC)
        self._tasks: set[asyncio.Task] = set()

        # Optional local voice-transcription settings
        self._voice_config: dict[str, Any] = voice_config or {}
        self._transcriber: Any = transcriber
        self._voice_enabled: bool = bool(self._voice_config.get("enabled", False))
        self._voice_max_audio_bytes: int = int(self._voice_config.get("max_audio_bytes", 25_000_000))
        self._voice_timeout_seconds: int = int(self._voice_config.get("timeout_seconds", 180))
        self._voice_language: str | None = self._voice_config.get("language")

    async def _set_display_name(self) -> None:
        """
        This function sets the bot's display name via the Matrix REST API.
        """
        url: str = f"{self._homeserver}/_matrix/client/v3/profile/{quote(string=self._user_id)}/displayname"
        headers: dict[str, str] = {"Authorization": f"Bearer {self._access_token}"}
        async with aiohttp.ClientSession() as session:
            async with session.put(url=url, headers=headers, json={"displayname": self._display_name}) as resp:
                if resp.status == 200:
                    logger.info("Display name set to '%s'", self._display_name)
                else:
                    logger.warning("Failed to set display name (status=%d)", resp.status)

    async def _join_room(self, room_id: str) -> None:
        """
        This function joins a room via the Matrix REST API.
        Uses raw HTTP because nio's join() sends a body Conduit rejects.
        """
        url: str = f"{self._homeserver}/_matrix/client/v3/join/{quote(string=room_id)}"
        headers: dict[str, str] = {"Authorization": f"Bearer {self._access_token}"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url=url, headers=headers, json={}) as resp:
                logger.info("Join %s: %d", room_id, resp.status)

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

    async def _typing_loop(self, room_id: str) -> None:
        """
        This function keeps the typing indicator alive in a room until cancelled.
        Matrix typing notifications expire after a timeout, so they must be refreshed
        periodically while the agent is working on a reply.
        """
        try:
            while True:
                try:
                    await self._client.room_typing(room_id=room_id, typing_state=True, timeout=30000)
                except Exception as e:
                    logger.debug("Failed to send typing notification in %s: %s", room_id, e)
                await asyncio.sleep(delay=20)
        except asyncio.CancelledError:
            pass

    async def _stop_typing(self, room_id: str) -> None:
        """
        This function clears the typing indicator in a room.
        """
        try:
            await self._client.room_typing(room_id=room_id, typing_state=False)
        except Exception as e:
            logger.debug("Failed to clear typing notification in %s: %s", room_id, e)

    async def _react_to_event(self, room_id: str, event_id: str, key: str) -> None:
        """
        This function sends an emoji reaction to a message via the Matrix API.
        """
        await self._client.room_send(
            room_id=room_id,
            message_type="m.reaction",
            content={
                "m.relates_to": {
                    "rel_type": "m.annotation",
                    "event_id": event_id,
                    "key": key
                }
            }
        )

    def _sanitize_sender(self, name: str) -> str:
        """
        This function sanitizes a sender display name to prevent prompt injection.
        Strips brackets and limits length.
        """
        return name.replace("[", "").replace("]", "").strip()[:50]

    def _get_sender_label(self, room: MatrixRoom, event: Any) -> str:
        """
        This function returns the display name for a message sender.
        Uses the room-level display name if available, otherwise the user ID.
        """
        display_name: str = room.user_name(event.sender) or event.sender
        return self._sanitize_sender(name=display_name)

    def _resolve_media_filename(self, event: Any) -> str:
        """
        This function derives a usable filename (with a correct extension) for an
        incoming media event. Matrix clients put the *caption* in event.body when a
        file is sent with text, so body is unreliable as a filename — an image sent
        with a caption would otherwise be saved with no extension, defeating type
        detection (e.g. vision attachment). The dedicated content.filename field is
        preferred; otherwise body is used only when it looks like a real filename
        (carries an extension); failing that a name is synthesised from the media id.
        In all cases a missing extension is filled in from the declared MIME type.
        """
        content: dict[str, Any] = {}
        try:
            content = event.source.get("content", {}) or {}
        except AttributeError:
            content = {}
        info: dict[str, Any] = content.get("info", {}) if isinstance(content.get("info"), dict) else {}

        mimetype: str = info.get("mimetype") or content.get("mimetype") or getattr(event, "mimetype", "") or ""
        ext_from_mime: str | None = mimetypes.guess_extension(mimetype.split(";")[0].strip()) if mimetype else None
        if ext_from_mime == ".jpe":
            ext_from_mime = ".jpg"

        # Choose a base name: explicit filename field, else body only if it carries an
        # extension (a true filename), else a stable base from the mxc media id.
        filename_field: str = os.path.basename(content.get("filename") or "").strip()
        body: str = os.path.basename(event.body or "").strip()

        candidate: str
        if filename_field:
            candidate = filename_field
        elif body and os.path.splitext(body)[1]:
            candidate = body
        else:
            candidate = (event.url or "").rsplit("/", 1)[-1] or "attachment"

        if not os.path.splitext(candidate)[1] and ext_from_mime:
            candidate = f"{candidate}{ext_from_mime}"

        return candidate or "attachment"

    def _extract_caption(self, event: Any) -> str:
        """
        This function returns the human caption attached to a media message, if any.
        When a file is sent with accompanying text, Matrix carries the caption in
        event.body (with the real name in content.filename), so the user's actual
        question would otherwise be lost. Bodies that are merely a filename (no caption
        text — they carry a file extension or match the dedicated filename field) yield
        an empty string.
        """
        content: dict[str, Any] = {}
        try:
            content = event.source.get("content", {}) or {}
        except AttributeError:
            content = {}

        body: str = (event.body or "").strip()
        if not body:
            return ""

        filename_field: str = content.get("filename") or ""
        if filename_field:
            # Extensible event: body is a caption unless it just repeats the filename.
            return "" if body == filename_field else self._sanitize_caption(body)

        # Legacy event: body is the filename. A real filename carries an extension;
        # anything else is human caption text.
        if os.path.splitext(body)[1]:
            return ""
        return self._sanitize_caption(body)

    @staticmethod
    def _sanitize_caption(text: str) -> str:
        """
        This function trims a caption and strips leading bracket characters so it cannot
        spoof the channel/file framing markers prepended to the message.
        """
        return text.lstrip("[]").strip()[:2000]

    async def _on_file(self, room: MatrixRoom, event: Any) -> None:
        """
        This function handles incoming file messages (m.file, m.image, m.audio, m.video).
        Downloads the file and passes a text message to the handler.
        """
        if event.sender == self._user_id:
            return
        if event.sender in self._bot_user_ids:
            return
        if event.server_timestamp < self._start_time:
            return
        if not self._attachments_dir:
            return

        # Get file info from the event
        mxc_url: str = event.url or ""
        filename: str = self._resolve_media_filename(event=event)

        if not mxc_url:
            return

        # Download the file
        filepath: str = await self._download_mxc(mxc_url=mxc_url, filename=filename)
        if not filepath:
            return

        # Build a text message telling the agent about the file (use actual saved filename)
        saved_filename: str = os.path.basename(filepath)
        sender_label: str = self._get_sender_label(room=room, event=event)
        caption: str = self._extract_caption(event=event)
        lines: list[str] = [
            f"[Channel: Matrix, Sender: {sender_label}]",
            f"[File received: attachments/{saved_filename}]",
        ]
        if caption:
            lines.append(caption)
        user_message: str = "\n".join(lines)

        logger.info("File received from %s in %s: %s", sender_label, room.room_id, saved_filename)

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

        def react(emoji: str) -> None:
            future = asyncio.run_coroutine_threadsafe(
                self._react_to_event(room_id=room.room_id, event_id=event.event_id, key=emoji),
                loop
            )
            future.result(timeout=10)

        async def _process() -> None:
            typing_task: asyncio.Task = asyncio.create_task(self._typing_loop(room_id=room.room_id))
            try:
                reply: str = await asyncio.to_thread(self._handler, user_message, room.room_id, notify, send_file, ask, react)
                await self._client.room_send(
                    room_id=room.room_id,
                    message_type="m.room.message",
                    content={"msgtype": "m.text", "body": reply}
                )
            except Exception as e:
                logger.error("Error processing file message in %s: %s", room.room_id, e, exc_info=True)
            finally:
                typing_task.cancel()
                await self._stop_typing(room_id=room.room_id)

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

        # Ignore messages from other Memtrix agents (prevents loops)
        if event.sender in self._bot_user_ids:
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

        def react(emoji: str) -> None:
            """
            React to the current message with an emoji.
            """
            future = asyncio.run_coroutine_threadsafe(
                self._react_to_event(room_id=room.room_id, event_id=event.event_id, key=emoji),
                loop
            )
            future.result(timeout=10)

        # Add channel and sender header to the message
        sender_label: str = self._get_sender_label(room=room, event=event)
        prefixed_message: str = f"[Channel: Matrix, Sender: {sender_label}]\n{event.body}"

        logger.info("Message from %s in %s: %s", sender_label, room.room_id, event.body[:100])

        # Process the message as a background task so sync_forever can continue
        async def _process() -> None:
            typing_task: asyncio.Task = asyncio.create_task(self._typing_loop(room_id=room.room_id))
            try:
                reply: str = await asyncio.to_thread(self._handler, prefixed_message, room.room_id, notify, send_file, ask, react)
                await self._client.room_send(
                    room_id=room.room_id,
                    message_type="m.room.message",
                    content={"msgtype": "m.text", "body": reply}
                )
            except Exception as e:
                logger.error("Error processing message in %s: %s", room.room_id, e, exc_info=True)
            finally:
                typing_task.cancel()
                await self._stop_typing(room_id=room.room_id)

        task: asyncio.Task = asyncio.create_task(_process())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _on_audio(self, room: MatrixRoom, event: Any) -> None:
        """
        This function handles Matrix voice messages (m.audio). When voice STT is
        enabled, it downloads and transcribes audio locally, then forwards the
        transcript to the normal Memtrix handler.
        """
        if event.sender == self._user_id:
            return
        if event.sender in self._bot_user_ids:
            return
        if event.server_timestamp < self._start_time:
            return
        if not self._attachments_dir:
            return

        mxc_url: str = event.url or ""
        filename: str = event.body or "voice_message"
        if not mxc_url:
            return

        filepath: str = await self._download_mxc(mxc_url=mxc_url, filename=filename)
        if not filepath:
            return

        sender_label: str = self._get_sender_label(room=room, event=event)
        saved_filename: str = os.path.basename(filepath)
        base_header: str = f"[Channel: Matrix, Sender: {sender_label}]"

        # Fallback payload (always available)
        user_message: str = (
            f"{base_header}\n"
            f"[Voice message received: attachments/{saved_filename}]\n"
            "[Transcription unavailable.]"
        )

        if self._voice_enabled and self._transcriber is not None:
            try:
                if os.path.getsize(filepath) > self._voice_max_audio_bytes:
                    raise RuntimeError("Audio file too large for configured voice.max_audio_bytes limit.")

                result: dict[str, Any] = await asyncio.wait_for(
                    asyncio.to_thread(self._transcriber.transcribe, filepath, self._voice_language),
                    timeout=self._voice_timeout_seconds,
                )
                transcript: str = str(result.get("text", "") or "").strip()
                if result.get("ok") and transcript:
                    user_message = (
                        f"{base_header}\n"
                        f"[Voice transcription from attachments/{saved_filename}]\n"
                        f"{transcript}"
                    )
                else:
                    error_detail: str = str(result.get("error", "transcription failed"))
                    user_message = (
                        f"{base_header}\n"
                        f"[Voice message received: attachments/{saved_filename}]\n"
                        f"[Transcription failed: {error_detail}]"
                    )
            except asyncio.TimeoutError:
                user_message = (
                    f"{base_header}\n"
                    f"[Voice message received: attachments/{saved_filename}]\n"
                    "[Transcription timed out.]"
                )
            except Exception as e:
                user_message = (
                    f"{base_header}\n"
                    f"[Voice message received: attachments/{saved_filename}]\n"
                    f"[Transcription failed: {e}]"
                )

        logger.info("Audio received from %s in %s: %s", sender_label, room.room_id, saved_filename)

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

        def react(emoji: str) -> None:
            future = asyncio.run_coroutine_threadsafe(
                self._react_to_event(room_id=room.room_id, event_id=event.event_id, key=emoji),
                loop
            )
            future.result(timeout=10)

        async def _process() -> None:
            typing_task: asyncio.Task = asyncio.create_task(self._typing_loop(room_id=room.room_id))
            try:
                reply: str = await asyncio.to_thread(self._handler, user_message, room.room_id, notify, send_file, ask, react)
                await self._client.room_send(
                    room_id=room.room_id,
                    message_type="m.room.message",
                    content={"msgtype": "m.text", "body": reply}
                )
            except Exception as e:
                logger.error("Error processing audio message in %s: %s", room.room_id, e, exc_info=True)
            finally:
                typing_task.cancel()
                await self._stop_typing(room_id=room.room_id)

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

        # Capture the running loop so other threads (e.g. worker-result delivery)
        # can schedule sends onto it thread-safely.
        self._loop = asyncio.get_running_loop()

        # Register event and sync callbacks
        self._client.add_event_callback(self._on_message, RoomMessageText)
        self._client.add_event_callback(self._on_file, RoomMessageFile)
        self._client.add_event_callback(self._on_file, RoomMessageImage)
        self._client.add_event_callback(self._on_audio, RoomMessageAudio)
        self._client.add_event_callback(self._on_file, RoomMessageVideo)
        self._client.add_response_callback(self._on_sync, SyncResponse)

        # Record start time (milliseconds) to skip old messages
        self._start_time = time.time() * 1000

        # Set the display name and perform the initial sync, retrying while the
        # homeserver is still starting up or briefly unreachable (e.g. local Conduit
        # booting, or a transient network issue with an external homeserver).
        logger.info("Connecting to %s as %s...", self._homeserver, self._user_id)
        response: Any = None
        attempt: int = 0
        while True:
            attempt += 1
            try:
                await self._set_display_name()
                response = await self._client.sync(timeout=10000)
                break
            except Exception as e:
                if attempt == 1 or attempt % 10 == 0:
                    logger.warning("Homeserver not reachable yet (attempt %d): %s", attempt, e)
                await asyncio.sleep(delay=min(30, 2 * attempt))

        # Join any rooms we were invited to while offline
        for room_id in response.rooms.invite:
            await self._join_room(room_id=room_id)

        logger.info("Matrix channel ready — listening for messages")

        # Listen for new events indefinitely
        await self._client.sync_forever(timeout=30000)

    def run(self, handler: Callable[[str, str, Callable[[str], None], Callable[[str], None]], str]) -> None:
        """
        This function starts the Matrix event loop, calling handler for each incoming message.
        """
        self._handler = handler
        asyncio.run(main=self._run_async())

    def send_to_room(self, room_id: str, body: str, notice: bool = False) -> None:
        """
        This function delivers an unsolicited message to a room from any thread. It is
        used to push background worker results back into the originating conversation.
        The send is scheduled onto the channel's asyncio loop thread-safely and blocks
        briefly for confirmation. Safe to call before the loop is ready (no-op).
        """
        loop: asyncio.AbstractEventLoop | None = self._loop
        if loop is None or self._client is None:
            logger.warning("send_to_room called before Matrix channel was ready")
            return
        msgtype: str = "m.notice" if notice else "m.text"
        future = asyncio.run_coroutine_threadsafe(
            self._client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content={"msgtype": msgtype, "body": body},
            ),
            loop,
        )
        future.result(timeout=30)

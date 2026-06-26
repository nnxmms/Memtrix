#!/usr/bin/python3

"""Background mailbox poller that pings the main agent when new mail arrives."""

import logging
import threading
from typing import Any, Callable

from src.integrations.mail import EmailError, EmailManager

logger: logging.Logger = logging.getLogger(__name__)

# Lower bound on the poll interval so a misconfiguration can never hammer the IMAP
# server with sub-second checks.
_MIN_INTERVAL_SECONDS: int = 15


class MailPoller:
    """
    This is the MailPoller, a background daemon thread that periodically checks the
    mailbox for newly arrived unread messages and fires a trigger callback so the main
    agent can react to them — mirroring how a finished background worker pings the
    agent. It never marks messages read (it peeks), leaving the read state for the
    agent's own email_check. On startup it seeds a baseline of the currently-unread
    UIDs without notifying, so an existing backlog (or mail that arrived while Memtrix
    was down) does not trigger a flood of notifications; only genuinely new arrivals do.
    """

    def __init__(self, email_manager: EmailManager, trigger: Callable[..., None],
                 interval_seconds: int = 60, max_announce: int = 5) -> None:
        """
        This function builds the poller around an EmailManager and a trigger callback
        invoked as trigger(count, summary, uids) whenever new unread mail appears.
        """
        # Configured mailbox access (stateless; opens a fresh connection per check)
        self._email_manager: EmailManager = email_manager

        # Callback fired when new mail is detected: trigger(count, summary, uids)
        self._trigger: Callable[..., None] = trigger

        # How often to poll, clamped to a sane minimum
        self._interval: int = max(int(interval_seconds), _MIN_INTERVAL_SECONDS)

        # Maximum number of messages summarised in a single notification
        self._max_announce: int = max(int(max_announce), 1)

        # UIDs already accounted for (baseline + previously announced)
        self._seen_uids: set[str] = set()

        # Background thread + stop signal
        self._thread: threading.Thread | None = None
        self._stop: threading.Event = threading.Event()

    def start(self) -> None:
        """
        This function seeds the baseline of currently-unread UIDs (without notifying)
        and starts the background polling thread. Safe to call once.
        """
        if self._thread is not None:
            return
        try:
            baseline: list[dict[str, Any]] = self._email_manager.check(unread_only=True, mark_read=False)
            self._seen_uids = {str(msg.get("uid", "")) for msg in baseline if msg.get("uid")}
            logger.info("Mail poller seeded with %d existing unread message(s)", len(self._seen_uids))
        except Exception as exc:
            # A failed baseline (e.g. transient network/auth error) is non-fatal: start
            # with an empty baseline; the first poll will reconcile.
            logger.warning("Mail poller baseline check failed: %s", exc)
            self._seen_uids = set()

        self._thread = threading.Thread(target=self._loop, name="mail-poller", daemon=True)
        self._thread.start()
        logger.info("Mail poller started (every %ds)", self._interval)

    def stop(self) -> None:
        """
        This function signals the polling thread to exit at the next opportunity.
        """
        self._stop.set()

    def _loop(self) -> None:
        """
        This function is the polling loop: it waits one interval, checks for new unread
        mail, and fires the trigger for any UIDs not seen before.
        """
        while not self._stop.wait(timeout=self._interval):
            try:
                self._poll_once()
            except Exception as exc:
                # Never let a transient error kill the poller thread.
                logger.warning("Mail poll failed: %s", exc)

    def _poll_once(self) -> None:
        """
        This function performs a single mailbox check and notifies on new arrivals.
        """
        try:
            messages: list[dict[str, Any]] = self._email_manager.check(unread_only=True, mark_read=False)
        except EmailError as exc:
            logger.warning("Mail poll check failed: %s", exc)
            return

        # Messages come newest-first; reverse so notifications read oldest-first.
        new_messages: list[dict[str, Any]] = [
            msg for msg in reversed(messages) if str(msg.get("uid", "")) and str(msg["uid"]) not in self._seen_uids
        ]
        if not new_messages:
            return

        new_uids: list[str] = [str(msg["uid"]) for msg in new_messages]
        self._seen_uids.update(new_uids)

        summary: str = self._summarise(new_messages)
        try:
            self._trigger(count=len(new_messages), summary=summary, uids=new_uids)
        except Exception as exc:
            logger.error("Mail notification trigger failed: %s", exc, exc_info=True)

    def _summarise(self, messages: list[dict[str, Any]]) -> str:
        """
        This function builds a short, plain-text summary listing up to max_announce of
        the new messages as "From — Subject" lines, with an overflow note if truncated.
        """
        lines: list[str] = []
        for msg in messages[: self._max_announce]:
            sender: str = str(msg.get("from") or "(unknown sender)").strip()
            subject: str = str(msg.get("subject") or "(no subject)").strip()
            lines.append(f"- {sender} — {subject}")
        remaining: int = len(messages) - self._max_announce
        if remaining > 0:
            lines.append(f"- …and {remaining} more")
        return "\n".join(lines)

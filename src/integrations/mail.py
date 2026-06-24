#!/usr/bin/python3

"""Email integration: IMAP reading (with read/unread control) and SMTP sending."""

import email
import imaplib
import logging
import re
import smtplib
import ssl
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.utils import formataddr, getaddresses, parseaddr
from typing import Any

logger: logging.Logger = logging.getLogger(__name__)

# Config placeholder the web panel writes for the mailbox password. The real value
# is supplied as a secret (MEMTRIX_SECRET_EMAIL_PASSWORD / Bitwarden EMAIL_PASSWORD)
# and resolved into the config at startup, so it is never stored in config.json.
EMAIL_PASSWORD_PLACEHOLDER: str = "$EMAIL_PASSWORD"

# Network timeout (seconds) for IMAP/SMTP operations so a tool never hangs forever.
_NET_TIMEOUT: int = 30

# Strips control characters from header values to prevent header injection.
_HEADER_CONTROL_RE: re.Pattern[str] = re.compile(r"[\r\n\x00]")

# Crude HTML-to-text fallback when an email has no plain-text part.
_TAG_RE: re.Pattern[str] = re.compile(r"<[^>]+>")
_WS_RE: re.Pattern[str] = re.compile(r"[ \t]*\n[ \t]*")

# Tool files implementing the email capability. Gated behind email.enabled and
# reserved for the main agent (excluded from sub-agents), mirroring the SSH tools.
MAIL_TOOL_FILES: set[str] = {
    "email_check_tool.py",
    "email_mark_unread_tool.py",
    "email_send_tool.py",
}


class EmailError(Exception):
    """This is the EmailError raised for any IMAP/SMTP failure with a friendly message."""


def _decode_header_value(raw: str | None) -> str:
    """
    This function decodes an RFC 2047 encoded email header into a plain string.
    """
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def _html_to_text(html: str) -> str:
    """
    This function converts an HTML email body to readable plain text as a fallback
    when no text/plain part is present.
    """
    text: str = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = _TAG_RE.sub("", text)
    text = (
        text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
        .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    )
    text = _WS_RE.sub("\n", text)
    return text.strip()


def _extract_body(message: Message, max_chars: int) -> str:
    """
    This function extracts a readable plain-text body from an email message,
    preferring text/plain and falling back to a stripped text/html part.
    """
    plain: str = ""
    html: str = ""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disposition: str = str(part.get("Content-Disposition") or "")
            if "attachment" in disposition.lower():
                continue
            content_type: str = part.get_content_type()
            if content_type not in ("text/plain", "text/html"):
                continue
            payload: Any = part.get_payload(decode=True)
            if payload is None:
                continue
            charset: str = part.get_content_charset() or "utf-8"
            try:
                decoded: str = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                decoded = payload.decode("utf-8", errors="replace")
            if content_type == "text/plain" and not plain:
                plain = decoded
            elif content_type == "text/html" and not html:
                html = decoded
    else:
        payload = message.get_payload(decode=True)
        charset = message.get_content_charset() or "utf-8"
        if payload is not None:
            try:
                decoded = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                decoded = payload.decode("utf-8", errors="replace")
            if message.get_content_type() == "text/html":
                html = decoded
            else:
                plain = decoded

    body: str = plain.strip() or _html_to_text(html)
    body = body.strip()
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "\n…[truncated]"
    return body


def _sanitize_header(value: str) -> str:
    """
    This function removes CR/LF/NUL from a header value to prevent header injection.
    """
    return _HEADER_CONTROL_RE.sub("", value).strip()


def _parse_recipients(raw: str) -> list[str]:
    """
    This function parses a comma/semicolon-separated recipient string into a list of
    valid email addresses, raising EmailError when an address is malformed.
    """
    cleaned: str = _sanitize_header(raw).replace(";", ",")
    addresses: list[str] = []
    for _name, addr in getaddresses([cleaned]):
        addr = addr.strip()
        if not addr:
            continue
        if "@" not in addr or " " in addr:
            raise EmailError(f"Invalid email address: '{addr}'.")
        addresses.append(addr)
    if not addresses:
        raise EmailError("No valid recipient addresses were provided.")
    return addresses


class EmailManager:

    def __init__(self, config: dict[str, Any]) -> None:
        """
        This is the EmailManager which reads a mailbox over IMAP and sends mail over
        SMTP. It is constructed from the already-resolved email configuration, so the
        password it receives is the real secret value (never a placeholder).
        """
        self._imap_host: str = str(config.get("imap_host") or "").strip()
        self._imap_port: int = int(config.get("imap_port") or 993)
        self._imap_ssl: bool = bool(config.get("imap_ssl", True))
        self._smtp_host: str = str(config.get("smtp_host") or "").strip()
        self._smtp_port: int = int(config.get("smtp_port") or 587)
        self._smtp_security: str = str(config.get("smtp_security") or "starttls").strip().lower()
        self._username: str = str(config.get("username") or "").strip()
        self._from_address: str = str(config.get("from_address") or "").strip() or self._username
        self._from_name: str = _sanitize_header(str(config.get("from_name") or "").strip())
        password: str = str(config.get("password") or "")
        # An unresolved placeholder means the secret was never set — treat as empty.
        self._password: str = "" if password.strip() == EMAIL_PASSWORD_PLACEHOLDER else password
        self._mailbox: str = str(config.get("mailbox") or "INBOX").strip() or "INBOX"
        self._auto_mark_read: bool = bool(config.get("auto_mark_read", True))
        self._max_fetch: int = max(1, int(config.get("max_fetch") or 10))
        self._max_body_chars: int = max(200, int(config.get("max_body_chars") or 4000))

    # ------------------------------------------------------------------- readiness

    def _require_imap(self) -> None:
        """
        This function raises EmailError when IMAP reading is not fully configured.
        """
        missing: list[str] = []
        if not self._imap_host:
            missing.append("IMAP host")
        if not self._username:
            missing.append("username")
        if not self._password:
            missing.append("password (set the EMAIL_PASSWORD secret)")
        if missing:
            raise EmailError("Email is not fully configured — missing: " + ", ".join(missing) + ".")

    def _require_smtp(self) -> None:
        """
        This function raises EmailError when SMTP sending is not fully configured.
        """
        missing: list[str] = []
        if not self._smtp_host:
            missing.append("SMTP host")
        if not self._username:
            missing.append("username")
        if not self._password:
            missing.append("password (set the EMAIL_PASSWORD secret)")
        if missing:
            raise EmailError("Email is not fully configured — missing: " + ", ".join(missing) + ".")

    # ------------------------------------------------------------------ connections

    def _imap_connect(self) -> imaplib.IMAP4:
        """
        This function opens and authenticates an IMAP connection.
        """
        try:
            client: imaplib.IMAP4
            if self._imap_ssl:
                context: ssl.SSLContext = ssl.create_default_context()
                client = imaplib.IMAP4_SSL(
                    host=self._imap_host, port=self._imap_port,
                    ssl_context=context, timeout=_NET_TIMEOUT,
                )
            else:
                client = imaplib.IMAP4(host=self._imap_host, port=self._imap_port, timeout=_NET_TIMEOUT)
                client.starttls(ssl.create_default_context())
            client.login(self._username, self._password)
            return client
        except imaplib.IMAP4.error as exc:
            raise EmailError(f"IMAP login failed: {exc}") from exc
        except OSError as exc:
            raise EmailError(f"Could not connect to IMAP server {self._imap_host}:{self._imap_port}: {exc}") from exc

    def _smtp_connect(self) -> smtplib.SMTP:
        """
        This function opens and authenticates an SMTP connection using the configured
        transport security (STARTTLS, implicit SSL, or none).
        """
        try:
            smtp: smtplib.SMTP
            if self._smtp_security == "ssl":
                smtp = smtplib.SMTP_SSL(
                    host=self._smtp_host, port=self._smtp_port,
                    context=ssl.create_default_context(), timeout=_NET_TIMEOUT,
                )
            else:
                smtp = smtplib.SMTP(host=self._smtp_host, port=self._smtp_port, timeout=_NET_TIMEOUT)
                smtp.ehlo()
                if self._smtp_security == "starttls":
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
            smtp.login(self._username, self._password)
            return smtp
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailError(f"SMTP authentication failed: {exc}") from exc
        except smtplib.SMTPException as exc:
            raise EmailError(f"SMTP error: {exc}") from exc
        except OSError as exc:
            raise EmailError(f"Could not connect to SMTP server {self._smtp_host}:{self._smtp_port}: {exc}") from exc

    # ---------------------------------------------------------------------- actions

    def check(self, unread_only: bool = True, limit: int | None = None,
              mark_read: bool | None = None) -> list[dict[str, Any]]:
        """
        This function retrieves the most recent messages from the mailbox (unread only
        by default) without altering their read state during the fetch (it uses
        BODY.PEEK), then marks the retrieved messages as read unless told otherwise.
        Returns a list of message dicts (uid, from, to, subject, date, body, seen).
        """
        self._require_imap()
        count: int = limit if isinstance(limit, int) and limit > 0 else self._max_fetch
        count = min(count, 50)
        do_mark_read: bool = self._auto_mark_read if mark_read is None else mark_read

        client: imaplib.IMAP4 = self._imap_connect()
        try:
            status, _ = client.select(f'"{self._mailbox}"')
            if status != "OK":
                raise EmailError(f"Mailbox '{self._mailbox}' could not be opened.")

            criteria: str = "UNSEEN" if unread_only else "ALL"
            status, data = client.uid("SEARCH", None, criteria)
            if status != "OK":
                raise EmailError("IMAP search failed.")

            uids: list[bytes] = data[0].split() if data and data[0] else []
            uids = uids[-count:]  # most recent
            uids.reverse()        # newest first

            messages: list[dict[str, Any]] = []
            retrieved_uids: list[bytes] = []
            for uid in uids:
                status, fetched = client.uid("FETCH", uid, "(BODY.PEEK[])")
                if status != "OK" or not fetched or not isinstance(fetched[0], tuple):
                    continue
                raw_bytes: bytes = fetched[0][1]
                parsed: Message = email.message_from_bytes(raw_bytes)
                messages.append({
                    "uid": uid.decode(),
                    "from": _decode_header_value(parsed.get("From")),
                    "to": _decode_header_value(parsed.get("To")),
                    "subject": _decode_header_value(parsed.get("Subject")) or "(no subject)",
                    "date": _decode_header_value(parsed.get("Date")),
                    "body": _extract_body(message=parsed, max_chars=self._max_body_chars),
                    "seen": False if unread_only else None,
                })
                retrieved_uids.append(uid)

            if do_mark_read and retrieved_uids:
                for uid in retrieved_uids:
                    client.uid("STORE", uid, "+FLAGS", "(\\Seen)")
                for msg in messages:
                    msg["seen"] = True

            return messages
        finally:
            self._safe_logout(client)

    def mark_unread(self, uids: list[str]) -> int:
        """
        This function clears the \\Seen flag on the given message UIDs so they appear
        as unread again. Returns the number of messages successfully updated.
        """
        self._require_imap()
        clean_uids: list[str] = [u.strip() for u in uids if u and u.strip().isdigit()]
        if not clean_uids:
            raise EmailError("Provide one or more numeric message UIDs (from email_check).")

        client: imaplib.IMAP4 = self._imap_connect()
        try:
            status, _ = client.select(f'"{self._mailbox}"')
            if status != "OK":
                raise EmailError(f"Mailbox '{self._mailbox}' could not be opened.")
            updated: int = 0
            for uid in clean_uids:
                status, _ = client.uid("STORE", uid, "-FLAGS", "(\\Seen)")
                if status == "OK":
                    updated += 1
            return updated
        finally:
            self._safe_logout(client)

    def send(self, to: str, subject: str, body: str,
             cc: str | None = None, bcc: str | None = None) -> str:
        """
        This function sends a plain-text email and returns a short confirmation string.
        """
        self._require_smtp()
        from_addr: str = parseaddr(self._from_address)[1] or self._from_address
        if "@" not in from_addr:
            raise EmailError("The configured from address is not a valid email address.")

        to_list: list[str] = _parse_recipients(to)
        cc_list: list[str] = _parse_recipients(cc) if cc and cc.strip() else []
        bcc_list: list[str] = _parse_recipients(bcc) if bcc and bcc.strip() else []
        recipients: list[str] = to_list + cc_list + bcc_list

        message: EmailMessage = EmailMessage()
        message["From"] = formataddr((self._from_name, from_addr)) if self._from_name else from_addr
        message["To"] = ", ".join(to_list)
        if cc_list:
            message["Cc"] = ", ".join(cc_list)
        message["Subject"] = _sanitize_header(subject)
        message.set_content(body)

        smtp: smtplib.SMTP = self._smtp_connect()
        try:
            smtp.send_message(message, from_addr=from_addr, to_addrs=recipients)
        except smtplib.SMTPException as exc:
            raise EmailError(f"Failed to send email: {exc}") from exc
        finally:
            try:
                smtp.quit()
            except Exception:
                pass
        return f"Email sent to {', '.join(recipients)}."

    def verify(self) -> str:
        """
        This function performs a lightweight IMAP login + mailbox select to verify the
        configuration, returning a human-readable status string.
        """
        self._require_imap()
        client: imaplib.IMAP4 = self._imap_connect()
        try:
            status, data = client.select(f'"{self._mailbox}"', readonly=True)
            if status != "OK":
                raise EmailError(f"Connected, but mailbox '{self._mailbox}' could not be opened.")
            total: str = data[0].decode() if data and data[0] else "?"
            return f"Connected to {self._imap_host} as {self._username}. Mailbox '{self._mailbox}' has {total} messages."
        finally:
            self._safe_logout(client)

    @staticmethod
    def _safe_logout(client: imaplib.IMAP4) -> None:
        """
        This function closes and logs out of an IMAP connection, ignoring errors.
        """
        try:
            client.close()
        except Exception:
            pass
        try:
            client.logout()
        except Exception:
            pass

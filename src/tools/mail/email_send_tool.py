#!/usr/bin/python3

from typing import Any

from src.integrations.mail import EmailError, EmailManager
from src.tools.base import BaseTool
from src.tools.utils import confirm_with_user


class EmailSendTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the EmailSendTool which sends an email over SMTP.
        """
        self._workspace_dir: str = workspace_dir
        self._email_manager: EmailManager | None = None
        super().__init__(
            name="email_send",
            description=(
                "Send a plain-text email. Provide one or more recipients (comma-separated) in 'to', "
                "a 'subject' and the 'body'. Optional 'cc' and 'bcc' recipients are supported. The "
                "user is asked to confirm before the message is actually sent."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient address(es), comma-separated.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "The email subject line.",
                    },
                    "body": {
                        "type": "string",
                        "description": "The plain-text body of the email.",
                    },
                    "cc": {
                        "type": "string",
                        "description": "Optional CC address(es), comma-separated.",
                    },
                    "bcc": {
                        "type": "string",
                        "description": "Optional BCC address(es), comma-separated.",
                    },
                },
                "required": ["to", "subject", "body"],
            },
        )

    def set_email_manager(self, manager: EmailManager) -> None:
        """
        This function injects the configured EmailManager (called at startup).
        """
        self._email_manager = manager

    def execute(self, **kwargs: Any) -> str:
        """
        This function sends an email after the user confirms.
        """
        if self._email_manager is None:
            return "Error: email is not enabled."

        to: str = str(kwargs.get("to") or "").strip()
        subject: str = str(kwargs.get("subject") or "").strip()
        body: str = str(kwargs.get("body") or "")
        cc: str = str(kwargs.get("cc") or "").strip()
        bcc: str = str(kwargs.get("bcc") or "").strip()

        if not to:
            return "Error: a recipient ('to') is required."
        if not subject:
            return "Error: a subject is required."
        if not body.strip():
            return "Error: the email body is empty."

        recipients_line: str = to + (f", cc {cc}" if cc else "") + (f", bcc {bcc}" if bcc else "")
        confirm_msg: str = (
            f"✉️ Send email?\n\n"
            f"To: {recipients_line}\n"
            f"Subject: {subject}\n\n"
            f"{body[:500]}{'…' if len(body) > 500 else ''}\n\n(yes/no)"
        )
        if not confirm_with_user(kwargs, message=confirm_msg):
            return "Email cancelled by user."

        try:
            return self._email_manager.send(to=to, subject=subject, body=body, cc=cc or None, bcc=bcc or None)
        except EmailError as exc:
            return f"Error: {exc}"

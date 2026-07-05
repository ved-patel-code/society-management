"""Test-mode email sender: renders the message to the log (docs/PF §9).

The dev default. Lets forgot-password / default-password flows be verified
end-to-end without any real provider.
"""
from __future__ import annotations

import logging

from app.core.email.base import EmailMessage, EmailSender

logger = logging.getLogger("app.email")


class TestEmailSender(EmailSender):
    def send(self, message: EmailMessage) -> None:
        logger.info(
            "[TEST EMAIL]\n  To: %s\n  Subject: %s\n  Body:\n%s",
            message.to,
            message.subject,
            message.body,
        )

"""Email interface + implementations + config-driven factory (docs/PF §9).

One place to swap providers. ``test`` mode renders emails to the log so flows are
verifiable without any provider (the dev default); ``smtp`` is a wired-later stub.
Callers depend on :class:`EmailSender`, never on a concrete class.
"""
from __future__ import annotations

from app.core.config import settings
from app.core.email.base import EmailMessage, EmailSender
from app.core.email.test_sender import TestEmailSender
from app.core.email.smtp_sender import SmtpEmailSender


def get_email_sender() -> EmailSender:
    """Return the configured sender. Injected as a FastAPI dependency."""
    if settings.email_mode == "smtp":
        return SmtpEmailSender()
    return TestEmailSender()


__all__ = [
    "EmailMessage",
    "EmailSender",
    "TestEmailSender",
    "SmtpEmailSender",
    "get_email_sender",
]

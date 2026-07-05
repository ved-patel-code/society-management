"""SMTP email sender — STUB (wired later; docs/PF §9, §15 deferred).

Present so the factory can select it via ``EMAIL_MODE=smtp`` without any caller
change once real SMTP is implemented. Until then it raises, so misconfiguration
is loud rather than silent.
"""
from __future__ import annotations

from app.core.email.base import EmailMessage, EmailSender


class SmtpEmailSender(EmailSender):
    def send(self, message: EmailMessage) -> None:  # pragma: no cover - stub
        raise NotImplementedError(
            "SMTP email is not implemented yet. Use EMAIL_MODE=test for now."
        )

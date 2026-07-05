"""Shared field validators (docs/03 §6). Reused across module schemas."""
from __future__ import annotations

import re

from app.common.errors import ValidationError

# Pragmatic email shape check. The DB enforces uniqueness (CITEXT UNIQUE);
# deliverability is not our concern here.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Password policy: min length + at least one letter and one digit (docs/PF §4).
MIN_PASSWORD_LENGTH = 8


def normalize_email(email: str) -> str:
    """Trim + lowercase. Login identity is case-insensitive (users.email CITEXT)."""
    normalized = email.strip().lower()
    if not _EMAIL_RE.match(normalized):
        raise ValidationError("Invalid email address.", details={"field": "email"})
    return normalized


def validate_password_policy(password: str) -> None:
    """Enforce the minimum password policy. Raises ``ValidationError`` on failure.

    Callers additionally enforce "new password must differ from the default/temp"
    (docs/PF §4) — that comparison needs the old hash and lives in the auth service.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
            details={"field": "password"},
        )
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValidationError(
            "Password must contain at least one letter and one digit.",
            details={"field": "password"},
        )

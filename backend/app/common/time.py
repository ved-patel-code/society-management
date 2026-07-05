"""Time helpers. Always work in timezone-aware UTC."""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)

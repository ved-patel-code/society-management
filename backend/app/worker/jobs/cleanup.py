"""Foundation worker job: purge dead auth rows (docs/PF §13).

Deletes expired/revoked ``refresh_tokens`` and consumed/expired ``password_resets``.
Idempotent — safe to run repeatedly; deleting already-gone rows is a no-op.
"""
from __future__ import annotations

import logging

from sqlalchemy import delete, or_

from app.common.time import utcnow
from app.core.db import SessionLocal
from app.platform.models import PasswordReset, RefreshToken

logger = logging.getLogger("app.worker.cleanup")


def purge_expired_auth_rows() -> dict[str, int]:
    """Delete expired/revoked refresh tokens and consumed/expired resets."""
    now = utcnow()
    session = SessionLocal()
    try:
        tokens = session.execute(
            delete(RefreshToken).where(
                or_(
                    RefreshToken.expires_at < now,
                    RefreshToken.revoked_at.is_not(None),
                )
            )
        )
        resets = session.execute(
            delete(PasswordReset).where(
                or_(
                    PasswordReset.expires_at < now,
                    PasswordReset.consumed_at.is_not(None),
                )
            )
        )
        session.commit()
        result = {
            "refresh_tokens_deleted": tokens.rowcount or 0,
            "password_resets_deleted": resets.rowcount or 0,
        }
        logger.info("Auth cleanup: %s", result)
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

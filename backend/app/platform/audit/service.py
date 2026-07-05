"""AuditService — writes append-only audit rows (docs/PF §12, docs/03 §7).

Called by every service that performs a state-changing admin action. The row is
added to the SAME session/transaction as the change, so the action and its audit
record commit atomically (``get_session`` commits once at request end).

Foundation ships this minimal, complete version; feature agents call
``AuditService.record`` — they do not reimplement auditing.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.platform.models import AuditLog


class AuditService:
    """Thin, stateless writer over ``audit_log``. Instantiate per request with
    the active session, or call :meth:`record` with the session passed in.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        action: str,
        actor_user_id: int | None,
        society_id: int | None = None,
        entity_type: str | None = None,
        entity_id: int | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Append one audit row within the current transaction (never commits)."""
        entry = AuditLog(
            society_id=society_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before=before,
            after=after,
        )
        self._session.add(entry)
        # Flush so the row (and its id) exists within this txn without committing.
        self._session.flush()
        return entry

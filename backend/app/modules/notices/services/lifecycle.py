"""Notice lifecycle concern — WAVE B (docs/modules/notice-board.md §4/§6).

Owns the two explicit status-edge endpoints (pin/expiry live in Wave A's PATCH):
- ``POST /notices/{id}/publish``   draft → published.
- ``POST /notices/{id}/withdraw``  draft|published → withdrawn (soft-delete).

Business rules Wave B enforces (docs §3/§4):
- Publish routes through ``support.apply_publish`` (the SAME helper create uses)
  so ``published_at`` + the ``notice_posted`` emit are identical on both paths;
  publishing an already-published/withdrawn notice → 409 (guarded by
  ``assert_transition_allowed``). Audit ``notice.published``.
- Withdraw is a soft-delete: ``status='withdrawn'`` + ``withdrawn_at`` /
  ``withdrawn_by``; removed from residents' feed AND archive, retained for the
  admin archive + audit. Attachments are left in Vault. Withdrawing a
  withdrawn notice → 409. Audit ``notice.withdrawn``.

Both admin-only (``notices.publish``). Nonexistent id → 404.

FROZEN STUBS: Wave B fills the bodies, editing only THIS file + its own test file.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.common.errors import NotFoundError
from app.common.time import utcnow
from app.modules.notices.repository import NoticeRepository
from app.modules.notices.schemas import (
    STATUS_DRAFT,
    STATUS_WITHDRAWN,
    NoticeDetailOut,
)
from app.modules.notices.services import support
from app.platform.audit.service import AuditService

_ACTION_PUBLISHED = "notice.published"
_ACTION_WITHDRAWN = "notice.withdrawn"
_ENTITY = "notice"


def _iso(when: datetime | None) -> str | None:
    """Serialize a datetime to ISO-8601 for the JSON audit payload (matches the
    finance/complaints audit idiom — audit ``before``/``after`` are stored as
    JSON, so raw ``datetime`` values are stamped as ``.isoformat()`` strings)."""
    return when.isoformat() if when is not None else None


class LifecycleService:
    def __init__(self, session: Session, repo: NoticeRepository) -> None:
        self._session = session
        self._repo = repo

    def _require_notice(self, society_id: int, notice_id: int):
        """Fetch a society-scoped notice or 404 (no cross-tenant leak)."""
        notice = self._repo.get_notice(society_id, notice_id)
        if notice is None:
            raise NotFoundError(
                "Notice not found.", details={"notice_id": notice_id}
            )
        return notice

    def publish(
        self, society_id: int, notice_id: int, *, actor_user_id: int
    ) -> NoticeDetailOut:
        """Publish a draft (draft → published); emit ``notice_posted`` (§4/§6).

        Routes the write through ``support.apply_publish`` — THE single publish
        choke-point shared with create-with-``publish=true`` (Wave A) — so the
        transition guard (draft → published; an already-published/withdrawn
        notice → 409 via ``assert_transition_allowed``), the ``published_at``
        stamp, the ``status`` set, and the ``notice_posted`` emit are identical
        on both paths and fire exactly ONCE. Audits ``notice.published``.
        """
        notice = self._require_notice(society_id, notice_id)

        # The single publish write: guard + stamp published_at + set status +
        # emit notice_posted ONCE. Never re-stamped or re-emitted here.
        support.apply_publish(notice, session=self._session)
        self._session.flush()

        AuditService(self._session).record(
            action=_ACTION_PUBLISHED,
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type=_ENTITY,
            entity_id=notice.id,
            before={"status": STATUS_DRAFT},
            after={
                "status": notice.status,
                "published_at": _iso(notice.published_at),
            },
        )
        return support.assemble_detail(
            self._session,
            self._repo,
            notice,
            is_read=self._repo.has_read(society_id, notice_id, actor_user_id),
        )

    def withdraw(
        self, society_id: int, notice_id: int, *, actor_user_id: int
    ) -> NoticeDetailOut:
        """Soft-withdraw a notice (draft|published → withdrawn) (§4/§6).

        Legal from ``draft`` (discarding a draft) OR ``published`` — guarded by
        ``support.assert_transition_allowed``; withdrawing an already-withdrawn
        notice → 409 (``withdrawn`` is terminal). Sets ``status='withdrawn'`` +
        ``withdrawn_at``/``withdrawn_by``; the notice leaves both the residents'
        feed and the admin archive but is retained for audit. Attachments are
        left in Vault (the admin cleans up via Vault). Audits
        ``notice.withdrawn``.
        """
        notice = self._require_notice(society_id, notice_id)

        from_status = notice.status
        # Edge legality (actor-independent) — 409 if illegal from the current
        # state (e.g. a double-withdraw off the terminal ``withdrawn`` state).
        support.assert_transition_allowed(from_status, STATUS_WITHDRAWN)

        when = utcnow()
        notice.status = STATUS_WITHDRAWN
        notice.withdrawn_at = when
        notice.withdrawn_by = actor_user_id
        self._session.flush()

        AuditService(self._session).record(
            action=_ACTION_WITHDRAWN,
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type=_ENTITY,
            entity_id=notice.id,
            before={"status": from_status},
            after={"status": STATUS_WITHDRAWN, "withdrawn_at": _iso(when)},
        )
        return support.assemble_detail(
            self._session,
            self._repo,
            notice,
            is_read=self._repo.has_read(society_id, notice_id, actor_user_id),
        )

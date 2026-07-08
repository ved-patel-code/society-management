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

from sqlalchemy.orm import Session

from app.modules.notices.repository import NoticeRepository
from app.modules.notices.schemas import NoticeDetailOut


class LifecycleService:
    def __init__(self, session: Session, repo: NoticeRepository) -> None:
        self._session = session
        self._repo = repo

    def publish(
        self, society_id: int, notice_id: int, *, actor_user_id: int
    ) -> NoticeDetailOut:
        """Publish a draft (draft → published); emit ``notice_posted`` (§4/§6)."""
        raise NotImplementedError

    def withdraw(
        self, society_id: int, notice_id: int, *, actor_user_id: int
    ) -> NoticeDetailOut:
        """Soft-withdraw a notice (draft|published → withdrawn) (§4/§6)."""
        raise NotImplementedError

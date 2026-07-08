"""Notices CRUD + feed concern — WAVE A (docs/modules/notice-board.md §4/§6).

Owns compose/edit and the read feed:
- ``POST /notices``        create (draft default; ``publish=true`` publishes now).
- ``PATCH /notices/{id}``  edit title/body/is_pinned/expires_at.
- ``GET  /notices``        the active feed (residents) / filtered list (admin).
- ``GET  /notices/{id}``   detail; marks the notice read for the caller.

Business rules Wave A enforces (docs §4):
- ``body`` is sanitized via ``support.sanitize_body`` on BOTH create and edit —
  the single choke-point (stored value is XSS-safe).
- Create publishes via ``support.apply_publish`` (shared with Wave B) so the
  ``published_at`` stamp + ``notice_posted`` emit never diverge.
- Edit stamps ``last_edited_at`` ONLY when content (title/body) changes, not when
  pin/expiry alone change. At least one field required (else 422).
- Feed: residents see the ACTIVE feed only (``support.is_active``), pinned-first
  then ``published_at`` DESC, with per-caller ``is_read`` + ``unread_count`` (no
  N+1 via ``repo.attachment_counts_for`` + ``repo.read_notice_ids_for``). Admins
  (``notices.publish``) may filter by ``status`` / ``scope=active|archive`` and
  see their own drafts.
- Detail: DRAFTS are visible only to ``notices.publish`` holders — a resident
  id-guess for a draft/withdrawn notice → 404 (no existence leak). Opening a
  notice idempotently inserts a read row + calls ``events.mark_read_for``.
- Audit ``notice.created`` and ``notice.edited`` (before/after of title/body +
  whether pin/expiry changed).

FROZEN STUBS: Wave A fills the bodies, editing only THIS file + its own test file.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.notices.repository import NoticeRepository
from app.modules.notices.schemas import (
    NoticeCreateRequest,
    NoticeDetailOut,
    NoticeListOut,
    NoticeUpdateRequest,
)


class NoticesCrudService:
    def __init__(self, session: Session, repo: NoticeRepository) -> None:
        self._session = session
        self._repo = repo

    def create(
        self, society_id: int, req: NoticeCreateRequest, *, actor_user_id: int
    ) -> NoticeDetailOut:
        """Create a notice (draft, or published when ``req.publish``) (§4/§6)."""
        raise NotImplementedError

    def edit(
        self,
        society_id: int,
        notice_id: int,
        req: NoticeUpdateRequest,
        *,
        actor_user_id: int,
    ) -> NoticeDetailOut:
        """Edit a notice's title/body/pin/expiry (admin) (§4/§6)."""
        raise NotImplementedError

    def list_feed(
        self,
        society_id: int,
        *,
        caller_user_id: int,
        can_manage: bool,
        status: str | None,
        scope: str | None,
        offset: int,
        limit: int,
    ) -> NoticeListOut:
        """The active feed (residents) or filtered list (admin) (§6).

        ``can_manage`` = caller holds ``notices.publish`` (or super-admin): admins
        may pass ``status`` / ``scope`` and see drafts; residents always get the
        active feed regardless of the filters.
        """
        raise NotImplementedError

    def get_detail(
        self,
        society_id: int,
        notice_id: int,
        *,
        caller_user_id: int,
        can_manage: bool,
    ) -> NoticeDetailOut:
        """Notice detail; marks it read for the caller (clear-on-read) (§6).

        Drafts/withdrawn notices are visible only when ``can_manage``; otherwise
        404 (no existence leak).
        """
        raise NotImplementedError

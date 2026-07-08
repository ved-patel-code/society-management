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

from datetime import datetime

from sqlalchemy.orm import Session

from app.common.errors import ConflictError, NotFoundError, ValidationError
from app.modules.notices import events
from app.modules.notices.models import Notice
from app.modules.notices.repository import NoticeRepository
from app.modules.notices.schemas import (
    LIST_SCOPES,
    NOTICE_STATUSES,
    SCOPE_ARCHIVE,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    STATUS_WITHDRAWN,
    NoticeCreateRequest,
    NoticeDetailOut,
    NoticeListOut,
    NoticeUpdateRequest,
)
from app.modules.notices.services import support
from app.platform.audit.service import AuditService


def _iso_or_none(value: datetime | None) -> str | None:
    """ISO-8601 string for an audit JSONB field (a raw ``datetime`` is not JSON
    serializable), or ``None`` — used for the ``expires_at`` before/after."""
    return value.isoformat() if value is not None else None


class NoticesCrudService:
    def __init__(self, session: Session, repo: NoticeRepository) -> None:
        self._session = session
        self._repo = repo

    # --- compose ------------------------------------------------------------

    def create(
        self, society_id: int, req: NoticeCreateRequest, *, actor_user_id: int
    ) -> NoticeDetailOut:
        """Create a notice (draft, or published when ``req.publish``) (§4/§6).

        The body is sanitized through the single choke-point before storage
        (docs §4). Insert as ``draft`` first, then — if ``publish`` — route the
        publish through ``support.apply_publish`` so the ``published_at`` stamp +
        the ``notice_posted`` emit stay identical to the explicit publish
        endpoint (Wave B). The author has NOT implicitly opened it (no read row
        is inserted on create), so the returned detail carries ``is_read=False``.
        """
        notice = self._repo.add_notice(
            Notice(
                society_id=society_id,
                title=support.sanitize_title(req.title),
                body=support.sanitize_body(req.body),
                status=STATUS_DRAFT,
                is_pinned=req.is_pinned,
                expires_at=req.expires_at,
                created_by=actor_user_id,
            )
        )

        # Publish-on-create: the single publish write (stamp + emit). Runs only
        # after the flush above so ``notice.id`` is in the emitted payload.
        if req.publish:
            support.apply_publish(notice, at=support.utcnow())
            self._session.flush()

        AuditService(self._session).record(
            action="notice.created",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="notice",
            entity_id=notice.id,
            after={
                "id": notice.id,
                "status": notice.status,
                "title": notice.title,
            },
        )
        # The author has not opened their own notice — no read row exists yet.
        return support.assemble_detail(
            self._session, self._repo, notice, is_read=False
        )

    # --- edit ---------------------------------------------------------------

    def edit(
        self,
        society_id: int,
        notice_id: int,
        req: NoticeUpdateRequest,
        *,
        actor_user_id: int,
    ) -> NoticeDetailOut:
        """Edit a notice's title/body/pin/expiry (admin) (§4/§6).

        A ``withdrawn`` notice is terminal — editing one is a 409. At least one
        field must be provided (else 422). ``last_edited_at`` is stamped ONLY when
        CONTENT (title/body) actually changes — a pin/expiry-only edit does not
        move the "edited · <date>" marker (docs §4). ``expires_at`` is
        sentinel-aware: it is only touched when explicitly present in the request
        (``model_fields_set``), so an omitted ``expires_at`` never clears a set
        expiry while an explicit ``null`` clears it.
        """
        notice = self._require_notice(society_id, notice_id)

        # Withdrawn is terminal (docs §3): a soft-deleted notice cannot be edited.
        if notice.status == STATUS_WITHDRAWN:
            raise ConflictError(
                "A withdrawn notice cannot be edited.",
                details={"status": notice.status},
            )

        provided = req.model_fields_set
        expiry_provided = "expires_at" in provided
        # At least one editable field must be present (docs §6). ``expires_at`` is
        # in-set even when explicitly null (clear-to-null), so it counts.
        if (
            req.title is None
            and req.body is None
            and req.is_pinned is None
            and not expiry_provided
        ):
            raise ValidationError("Provide at least one field to edit.")

        before: dict = {}
        after: dict = {}
        content_changed = False
        meta_changed = False

        if req.title is not None:
            new_title = support.sanitize_title(req.title)
            if new_title != notice.title:
                before["title"] = notice.title
                after["title"] = new_title
                notice.title = new_title
                content_changed = True

        if req.body is not None:
            new_body = support.sanitize_body(req.body)
            if new_body != notice.body:
                before["body"] = notice.body
                after["body"] = new_body
                notice.body = new_body
                content_changed = True

        if req.is_pinned is not None and req.is_pinned != notice.is_pinned:
            before["is_pinned"] = notice.is_pinned
            after["is_pinned"] = req.is_pinned
            notice.is_pinned = req.is_pinned
            meta_changed = True

        # Expiry: only touched when explicitly provided (present in fields_set),
        # so an omitted value never silently clears a set expiry. The audit JSONB
        # holds ISO strings (a raw ``datetime`` is not JSON-serializable).
        if expiry_provided and req.expires_at != notice.expires_at:
            before["expires_at"] = _iso_or_none(notice.expires_at)
            after["expires_at"] = _iso_or_none(req.expires_at)
            notice.expires_at = req.expires_at
            meta_changed = True

        # ``last_edited_at`` tracks CONTENT edits only (docs §4) — a pin/expiry-only
        # change leaves the "edited · <date>" marker where it was.
        if content_changed:
            notice.last_edited_at = support.utcnow()

        # Only flush + audit when something actually changed; a no-op edit that
        # re-sends the current values is accepted but writes nothing.
        if content_changed or meta_changed:
            self._session.flush()
            AuditService(self._session).record(
                action="notice.edited",
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type="notice",
                entity_id=notice.id,
                before=before,
                after=after,
            )

        is_read = self._repo.has_read(society_id, notice.id, actor_user_id)
        return support.assemble_detail(
            self._session, self._repo, notice, is_read=is_read
        )

    # --- feed / list --------------------------------------------------------

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
        now = support.utcnow()

        if not can_manage:
            # Residents: the ACTIVE feed only — status/scope filters are ignored,
            # never a draft/withdrawn/expired leak (docs §6).
            rows, total = self._repo.list_notices(
                society_id,
                active_only=True,
                now=now,
                offset=offset,
                limit=limit,
            )
        elif scope is not None:
            # An admin ``scope`` is validated (active|archive) up front (docs §6).
            if scope not in LIST_SCOPES:
                raise ValidationError(
                    "Unknown scope; expected 'active' or 'archive'.",
                    details={"scope": scope},
                )
            if scope == SCOPE_ARCHIVE:
                rows, total = self._repo.list_notices(
                    society_id,
                    archive_only=True,
                    now=now,
                    offset=offset,
                    limit=limit,
                )
            else:  # scope == active
                rows, total = self._repo.list_notices(
                    society_id,
                    active_only=True,
                    now=now,
                    offset=offset,
                    limit=limit,
                )
        elif status is not None:
            # Admin status filter (incl. own ``draft``) — validated (docs §6).
            if status not in NOTICE_STATUSES:
                raise ValidationError(
                    "Unknown status filter.", details={"status": status}
                )
            rows, total = self._repo.list_notices(
                society_id,
                statuses=[status],
                offset=offset,
                limit=limit,
            )
        else:
            # Admin default view is the active feed (same as a resident's).
            rows, total = self._repo.list_notices(
                society_id,
                active_only=True,
                now=now,
                offset=offset,
                limit=limit,
            )

        page_ids = [n.id for n in rows]
        # Batched per-page lookups — no N+1 (docs §6).
        attachment_counts = self._repo.attachment_counts_for(society_id, page_ids)
        read_ids = self._repo.read_notice_ids_for(
            society_id, caller_user_id, page_ids
        )
        items = [
            support.assemble_list_item(
                n,
                attachment_count=attachment_counts.get(n.id, 0),
                is_read=n.id in read_ids,
            )
            for n in rows
        ]

        # The unread badge is the count of currently-ACTIVE notices the caller has
        # not opened — independent of the current page/filter (docs §6).
        unread_count = self._unread_count(society_id, caller_user_id, now=now)

        return NoticeListOut(items=items, total=total, unread_count=unread_count)

    def _unread_count(
        self, society_id: int, caller_user_id: int, *, now
    ) -> int:
        """Active notices the caller has NOT read (the unread badge, docs §6)."""
        active_ids = self._repo.active_notice_ids(society_id, now=now)
        if not active_ids:
            return 0
        read_ids = self._repo.read_notice_ids_for(
            society_id, caller_user_id, active_ids
        )
        return len(active_ids) - len(read_ids)

    # --- detail -------------------------------------------------------------

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
        404 (no existence leak — a resident id-guess for an unpublished notice is
        indistinguishable from a nonexistent one). A published notice (incl. an
        expired one — the resident may hold a direct link) is openable by anyone
        who can read. Opening idempotently inserts the read row and fires the
        clear-on-read signal.
        """
        notice = self._require_notice(society_id, notice_id)

        # Visibility: only published notices are visible to non-managers; a draft
        # or withdrawn id → 404 (same as nonexistent), never a 403 that would leak
        # the notice's existence (docs §6).
        if notice.status != STATUS_PUBLISHED and not can_manage:
            raise NotFoundError(
                "Notice not found.", details={"notice_id": notice_id}
            )

        # Clear-on-read (docs §4/§7): idempotent read-row insert + drop the
        # caller's pending ``notice`` alert (no-op until Notifications subscribes).
        self._repo.mark_read(
            society_id, notice.id, caller_user_id, at=support.utcnow()
        )
        events.mark_read_for(caller_user_id, "notice", notice.id)

        # They have now read it — reflect that in the returned detail.
        return support.assemble_detail(
            self._session, self._repo, notice, is_read=True
        )

    # --- helpers ------------------------------------------------------------

    def _require_notice(self, society_id: int, notice_id: int) -> Notice:
        notice = self._repo.get_notice(society_id, notice_id)
        if notice is None:
            raise NotFoundError(
                "Notice not found.", details={"notice_id": notice_id}
            )
        return notice

"""Shared Notice Board service internals (docs/modules/notice-board.md §3/§4/§7).

Small, focused helpers every concern reuses so logic lives in ONE place
(docs/03 §1). Implemented in the frozen core; the wave services call these and
never reimplement them:

- ``sanitize_body`` — the single choke-point through which a notice ``body``
  reaches the model (calls the Foundation ``common/html_sanitizer``). Both create
  and edit go through here so stored XSS cannot land (docs §4).
- ``apply_publish`` — THE single publish write: stamps ``published_at``, sets
  ``status='published'``, and emits ``notice_posted`` ONCE. Shared by
  create-with-``publish=true`` (Wave A) and the explicit publish endpoint
  (Wave B) so the timestamp + event never diverge.
- ``assert_transition_allowed`` — guards the status edge against
  ``ALLOWED_TRANSITIONS`` (docs §3); per-actor authorization is the caller's job.
- ``is_active`` / ``is_expired`` — the query-time expiry predicate (``expired`` is
  never stored, docs §3).
- ``assemble_detail`` / ``assemble_list_item`` / ``preview_url_or_none`` — the
  single view builders, with trashed-Vault-document-safe URL handling so no read
  path ever 500s because one attachment can't be previewed.
- ``current_owner_ids`` — reaches House & Occupancy via its service interface,
  never its tables (docs/05).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.common.errors import ConflictError, ValidationError
from app.common.html_sanitizer import sanitize_html, sanitize_plain_text
from app.common.time import utcnow
from app.modules.notices import events
from app.modules.notices.models import Notice
from app.modules.notices.repository import NoticeRepository
from app.modules.notices.schemas import (
    ALLOWED_TRANSITIONS,
    NOTICE_STATUSES,
    STATUS_PUBLISHED,
    NoticeAttachmentOut,
    NoticeDetailOut,
    NoticeListItemOut,
)


# --- sanitize ----------------------------------------------------------------


def sanitize_body(raw: str) -> str:
    """Sanitize a rich-text notice body to the safe allow-list (docs §4).

    THE single place a body is cleaned before storage — the stored value is
    already safe to render. Delegates to the Foundation ``common/html_sanitizer``
    (nh3). Any writer of ``Notice.body`` MUST route through here.

    A body with no visible text once markup is stripped (e.g. ``"   "`` or
    ``"<p></p>"``) → 422: a notice must carry real content, mirroring the
    title's blank guard (the request schema's ``min_length`` can't see through
    tags/whitespace).
    """
    if not sanitize_plain_text(raw).strip():
        raise ValidationError("Body must not be empty.")
    return sanitize_html(raw)


def sanitize_title(raw: str) -> str:
    """Strip ALL markup from a notice title (defense-in-depth).

    A title is plain text — it needs no formatting — so every tag is removed
    (keeping the visible text). THE single place a title is cleaned before
    storage; any writer of ``Notice.title`` MUST route through here. Guards
    against a ``<script>`` in a title being stored verbatim and later rendered
    into HTML/email/a notification.

    If stripping markup leaves the title blank (e.g. a title that was *only*
    tags like ``<script></script>``), that is a 422 — a notice must have a real
    title, matching the "title must not be blank" request-schema invariant.
    """
    cleaned = sanitize_plain_text(raw).strip()
    if not cleaned:
        raise ValidationError("Title must not be blank after removing markup.")
    return cleaned


# --- status transitions ------------------------------------------------------


def assert_transition_allowed(from_status: str, to_status: str) -> None:
    """Raise 409 if ``from_status -> to_status`` is not a legal edge (docs §3).

    The legal set is :data:`ALLOWED_TRANSITIONS` (actor-independent). Callers add
    their OWN actor authorization on top; this guards the edge itself so no
    service can invent an illegal transition.
    """
    if to_status not in NOTICE_STATUSES:
        raise ValidationError(
            "Unknown target status.", details={"to_status": to_status}
        )
    if to_status not in ALLOWED_TRANSITIONS.get(from_status, frozenset()):
        raise ConflictError(
            f"Cannot move a notice from '{from_status}' to '{to_status}'.",
            details={"from_status": from_status, "to_status": to_status},
        )


def apply_publish(
    notice: Notice, *, at: datetime | None = None, session=None
) -> None:
    """Publish ``notice`` (draft → published) and emit ``notice_posted`` ONCE.

    THE single publish choke-point (docs §4). Guards the transition, stamps
    ``published_at`` and ``status``, then emits the domain event with the
    doc-specified payload. Shared by create-with-``publish=true`` and the explicit
    publish endpoint so the stamp + emit never diverge between them.

    ``session`` (the caller's request session) is threaded to the Notifications
    subscriber so its fan-out writes commit in THIS transaction — the notice and
    its notifications are atomic (docs/05 §3). Per-actor authorization (the caller
    holds ``notices.publish``) is the caller's responsibility — this is the write,
    not the gate.
    """
    assert_transition_allowed(notice.status, STATUS_PUBLISHED)
    when = at or utcnow()
    notice.published_at = when
    notice.status = STATUS_PUBLISHED
    # Flush so the notice row (and its id) is visible to the subscriber's fan-out
    # query within this same transaction.
    if session is not None:
        session.flush()
    # Emit ``published_at`` as an ISO-8601 string (JSON-safe) so a future
    # Notifications subscriber can serialize the payload to a queue/worker
    # without special datetime handling — matches this module's audit idiom.
    events.emit_posted(
        {
            "notice_id": notice.id,
            "society_id": notice.society_id,
            "title": notice.title,
            "published_at": when.isoformat(),
        },
        session=session,
    )


# --- expiry predicate (computed, never stored) -------------------------------


def is_expired(notice: Notice, now: datetime) -> bool:
    """True if a published notice is past its ``expires_at`` (docs §3).

    ``expired`` is COMPUTED here, never a stored status: a published notice with
    ``expires_at <= now`` has left the active feed for the admin archive.
    """
    return notice.expires_at is not None and notice.expires_at <= now


def is_active(notice: Notice, now: datetime) -> bool:
    """True if a notice is on the ACTIVE feed: published and not expired (docs §4)."""
    return notice.status == STATUS_PUBLISHED and not is_expired(notice, now)


# --- Occupancy interface (never table access) --------------------------------


def current_owner_ids(session: Session, society_id: int) -> set[int]:
    """The society's CURRENT owner user ids (docs §7).

    The read-receipt denominator + the broadcast audience. Reaches House &
    Occupancy via its service interface (lazy import avoids an import cycle),
    never its tables (docs/05).
    """
    from app.modules.houses.service import HouseService

    return HouseService(session).current_owner_user_ids(society_id)


# --- view builders (trashed-attachment-safe) ---------------------------------


def preview_url_or_none(
    session: Session, society_id: int, document_id: int
) -> str | None:
    """A signed inline preview URL for an attachment, or ``None`` if Vault can't
    produce one — a trashed/purged document, or the Vault module disabled.

    A read path must NEVER 500 because one attachment can't be previewed.
    """
    from app.modules.vault import api as vault_api

    try:
        return vault_api.get_preview_url(session, society_id, document_id).url
    except Exception:
        return None


def download_url_or_none(
    session: Session, society_id: int, document_id: int
) -> str | None:
    """A signed download URL for an attachment, or ``None`` (see
    :func:`preview_url_or_none`)."""
    from app.modules.vault import api as vault_api

    try:
        return vault_api.get_download_url(session, society_id, document_id).url
    except Exception:
        return None


def assemble_attachments(
    session: Session, repo: NoticeRepository, notice: Notice
) -> list[NoticeAttachmentOut]:
    """Build a notice's attachment list with guarded signed URLs (docs §6)."""
    out: list[NoticeAttachmentOut] = []
    for att in repo.list_attachments(notice.society_id, notice.id):
        item = NoticeAttachmentOut.model_validate(att)
        item.preview_url = preview_url_or_none(
            session, notice.society_id, att.vault_document_id
        )
        item.download_url = download_url_or_none(
            session, notice.society_id, att.vault_document_id
        )
        out.append(item)
    return out


def assemble_detail(
    session: Session,
    repo: NoticeRepository,
    notice: Notice,
    *,
    is_read: bool,
) -> NoticeDetailOut:
    """Build the full :class:`NoticeDetailOut` for one notice (docs §6).

    THE single detail view builder — used by create/edit/publish/withdraw and
    get-detail so the shape + the trashed-attachment-safe URL handling never
    diverge. One attachment read; previews guarded. One notice at a time (no
    cross-notice N+1).
    """
    return NoticeDetailOut(
        id=notice.id,
        title=notice.title,
        body=notice.body,
        status=notice.status,
        is_pinned=notice.is_pinned,
        published_at=notice.published_at,
        expires_at=notice.expires_at,
        last_edited_at=notice.last_edited_at,
        created_by=notice.created_by,
        withdrawn_at=notice.withdrawn_at,
        withdrawn_by=notice.withdrawn_by,
        is_read=is_read,
        created_at=notice.created_at,
        updated_at=notice.updated_at,
        attachments=assemble_attachments(session, repo, notice),
    )


def assemble_list_item(
    notice: Notice, *, attachment_count: int, is_read: bool
) -> NoticeListItemOut:
    """Build one feed card from a notice + its batched counts (docs §6).

    The counts come from the repository's batched fetches (no N+1); this is a
    pure projection.
    """
    return NoticeListItemOut(
        id=notice.id,
        title=notice.title,
        status=notice.status,
        is_pinned=notice.is_pinned,
        published_at=notice.published_at,
        expires_at=notice.expires_at,
        last_edited_at=notice.last_edited_at,
        attachment_count=attachment_count,
        is_read=is_read,
        created_at=notice.created_at,
        updated_at=notice.updated_at,
    )

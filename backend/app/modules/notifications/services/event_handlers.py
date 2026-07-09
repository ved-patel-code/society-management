"""Event-driven notification handlers (docs/modules/notifications.md §4.2).

One handler per subscribed domain event. Each turns an emitter's event into
in-app notifications by resolving recipients (data-driven) and calling the engine.
Subscribed on the bus by ``handlers.register_all`` at startup.

Design (see ``handlers.py`` for the full frozen contract):
- Each handler runs INSIDE the emitter's transaction, using the ``session`` the
  emitter puts on the event payload (docs §4.1 — "handlers run inline, in the
  emitter's request transaction, so a handler that writes rows commits/rolls back
  atomically with the state change"). The handler NEVER commits or closes that
  session — ``get_session`` commits once at request end, so a complaint and its
  admin notifications are one atomic unit (no crash-window gap).
- A handler wraps its writes in a **SAVEPOINT** (``begin_nested``): a handler
  failure rolls back ONLY the handler's writes and is logged+swallowed, so a bad
  subscriber can never poison the emitter's transaction or bleed across
  societies (plan §7 — containment). The bus also swallows, but the SAVEPOINT is
  what keeps the emitter's own writes intact.
- Handlers work PURELY from the payload for source-entity fields; recipient
  resolution reads other tables (roles/occupancy) in the SAME session, which sees
  the emitter's just-written rows (same transaction — no visibility gap).
- Handlers are SOFT on optional modules: if the complaint/notice module isn't
  enabled for the society, resolution yields no recipients → a safe no-op.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.modules.notifications.services.engine import NotificationEngine
from app.modules.notifications.schemas import (
    ENTITY_COMPLAINT,
    ENTITY_NOTICE,
    TYPE_COMPLAINT_NEW,
    TYPE_COMPLAINT_UPDATE,
    TYPE_COMPLAINT_WITHDRAWN,
    TYPE_NOTICE,
)
from app.platform.roles.service import RoleService

logger = logging.getLogger("app.notifications.handlers")

# The permission whose holders are the "admins" for complaint alerts (docs §4.2).
_COMPLAINTS_ADMIN_PERM = "complaints.read_all"

# The reserved payload key the emitters put their request session on (docs §4.1).
_SESSION_KEY = "session"


def _in_emitter_session(fn):
    """Run ``fn(session, payload)`` in the EMITTER's session, inside a SAVEPOINT.

    The emitter puts its request ``Session`` on ``payload['session']`` so the
    handler joins the emitter's transaction (atomic with the source action, and
    the handler sees the emitter's just-written rows). The handler NEVER commits
    or closes — ``get_session`` owns the commit at request end.

    The handler's writes run in a nested transaction (SAVEPOINT). On success it
    releases; on error it rolls back to the savepoint (undoing ONLY the handler's
    partial writes) and logs+swallows, so a handler failure can never poison the
    emitter's transaction or bleed across societies. If no session is on the
    payload (a misconfigured emit), the handler is a logged no-op — never a crash.
    """

    def _wrapper(payload: dict[str, Any]) -> None:
        session: Session | None = payload.get(_SESSION_KEY)
        if session is None:
            logger.warning(
                "notification handler %s got no session on payload; skipping",
                getattr(fn, "__name__", "?"),
            )
            return
        try:
            with session.begin_nested():  # SAVEPOINT — isolates handler writes
                fn(session, payload)
        except Exception:  # pragma: no cover - defensive; logged, never re-raised
            # begin_nested already rolled back to the savepoint on the exception;
            # the emitter's outer transaction is intact.
            logger.exception(
                "notification handler %s failed (payload keys=%s)",
                getattr(fn, "__name__", "?"),
                sorted(k for k in payload.keys() if k != _SESSION_KEY),
            )

    _wrapper.__name__ = getattr(fn, "__name__", "handler")
    return _wrapper


# --- helpers ------------------------------------------------------------------


def _admin_recipients(session: Session, society_id: int) -> set[int]:
    """User ids that are complaint "admins" in the society (holders of
    ``complaints.read_all``). Empty when Complaints isn't enabled / no admin."""
    return RoleService(session).user_ids_with_permission(
        society_id, _COMPLAINTS_ADMIN_PERM
    )


def _complaint_society_id(session: Session, house_id: int) -> int | None:
    """Resolve a complaint's society from its house via the House interface.

    The complaint payload carries ``house_id`` but not ``society_id``; a house
    belongs to exactly one society. Returns None if the house can't be resolved
    (defensive — yields a no-op)."""
    from app.modules.houses.service import HouseService

    return HouseService(session).society_id_for_house(house_id)


# --- complaint handlers -------------------------------------------------------


@_in_emitter_session
def on_complaint_created(session: Session, payload: dict[str, Any]) -> None:
    """``complaint.created`` → ``complaint_new`` to the society's complaint admins
    (docs §4.2). Recipients = holders of ``complaints.read_all``."""
    house_id = payload["house_id"]
    society_id = _complaint_society_id(session, house_id)
    if society_id is None:
        return
    admins = _admin_recipients(session, society_id)
    reference = payload.get("reference")
    NotificationEngine(session).notify_many(
        society_id=society_id,
        user_ids=admins,
        type=TYPE_COMPLAINT_NEW,
        title="New complaint raised",
        body=f"A new complaint ({reference}) was raised.",
        payload={
            "complaint_id": payload["complaint_id"],
            "reference": reference,
            "house_id": house_id,
            "category_id": payload.get("category_id"),
        },
        ref=(ENTITY_COMPLAINT, payload["complaint_id"]),
    )


@_in_emitter_session
def on_complaint_withdrawn(session: Session, payload: dict[str, Any]) -> None:
    """``complaint.withdrawn`` → ``complaint_withdrawn`` to the complaint admins."""
    house_id = payload["house_id"]
    society_id = _complaint_society_id(session, house_id)
    if society_id is None:
        return
    admins = _admin_recipients(session, society_id)
    reference = payload.get("reference")
    NotificationEngine(session).notify_many(
        society_id=society_id,
        user_ids=admins,
        type=TYPE_COMPLAINT_WITHDRAWN,
        title="Complaint withdrawn",
        body=f"Complaint {reference} was withdrawn.",
        payload={
            "complaint_id": payload["complaint_id"],
            "reference": reference,
            "house_id": house_id,
        },
        ref=(ENTITY_COMPLAINT, payload["complaint_id"]),
    )


@_in_emitter_session
def on_complaint_status_changed(
    session: Session, payload: dict[str, Any]
) -> None:
    """``complaint.status_changed`` → ``complaint_update`` to the RAISING owner
    (docs §4.2). Single recipient = ``raised_by``."""
    house_id = payload["house_id"]
    society_id = _complaint_society_id(session, house_id)
    if society_id is None:
        return
    raised_by = payload.get("raised_by")
    if raised_by is None:
        return
    reference = payload.get("reference")
    to_status = payload.get("to_status")
    NotificationEngine(session).notify(
        society_id=society_id,
        user_id=raised_by,
        type=TYPE_COMPLAINT_UPDATE,
        title="Complaint update",
        body=f"Your complaint {reference} is now {to_status}.",
        payload={
            "complaint_id": payload["complaint_id"],
            "reference": reference,
            "from_status": payload.get("from_status"),
            "to_status": to_status,
            "note": payload.get("note"),
        },
        ref=(ENTITY_COMPLAINT, payload["complaint_id"]),
    )


# --- notice handler -----------------------------------------------------------


@_in_emitter_session
def on_notice_posted(session: Session, payload: dict[str, Any]) -> None:
    """``notice_posted`` → ``notice`` to ALL current owners (docs §4.2).

    One row per current owner, in a single batched insert (the fan-out scale
    path). ``society_id`` is carried in the payload."""
    from app.modules.houses.service import HouseService

    society_id = payload["society_id"]
    notice_id = payload["notice_id"]
    title = payload.get("title")
    owners = HouseService(session).current_owner_user_ids(society_id)
    NotificationEngine(session).notify_many(
        society_id=society_id,
        user_ids=owners,
        type=TYPE_NOTICE,
        title="New notice",
        body=title or "A new notice was posted.",
        payload={
            "notice_id": notice_id,
            "title": title,
            "published_at": payload.get("published_at"),
        },
        ref=(ENTITY_NOTICE, notice_id),
    )


# --- clear-on-read handler ----------------------------------------------------


@_in_emitter_session
def on_mark_read(session: Session, payload: dict[str, Any]) -> None:
    """``complaint.mark_read`` / ``notice.mark_read`` → clear the user's pending
    notifications for that entity (docs §4.4). Payload:
    ``user_id``, ``entity_type``, ``entity_id``."""
    NotificationEngine(session).clear_for_entity(
        user_id=payload["user_id"],
        entity_type=payload["entity_type"],
        entity_id=payload["entity_id"],
    )

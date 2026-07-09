"""Complaints domain-event call surface (docs/modules/complaints.md §6/§7, docs/05 §3).

Thin wrappers the complaints services call at the doc-specified sites. They route
to the shared in-process dispatcher (``app.common.events``); with Notifications
not yet built there are no subscribers, so every call is a safe no-op. When
Notifications is built it ``subscribe``s to these event names + a ``read`` event
at startup — with ZERO change to the call sites here (skeleton-then-wire).

Event names + payloads are the contract in docs §7 / docs/05 §3:
- ``complaint.created``       -> admins' ``complaint_new`` alert
- ``complaint.withdrawn``     -> admins' ``complaint_withdrawn`` alert
- ``complaint.status_changed``-> the raising owner's ``complaint_update``
- ``mark_read_for``           -> clear-on-read (raiser opens -> clears their
  ``complaint_update``; admin opens -> clears their ``complaint_new`` /
  ``complaint_withdrawn``).
"""
from __future__ import annotations

from typing import Any

from app.common import events as _bus

EVENT_CREATED = "complaint.created"
EVENT_WITHDRAWN = "complaint.withdrawn"
EVENT_STATUS_CHANGED = "complaint.status_changed"
# The clear-on-read signal (Notifications subscribes; no-op until then).
EVENT_MARK_READ = "complaint.mark_read"


def emit_created(payload: dict[str, Any], *, session=None) -> None:
    """Emit ``complaint.created`` (payload: complaint_id, house_id, raised_by,
    reference, category_id).

    ``session`` (the emitter's request session) is threaded to the subscriber on
    the payload so a Notifications handler writes its rows in THIS transaction —
    the complaint and its notifications commit atomically (docs/05 §3, docs
    notifications §4.1). Optional/back-compat: with no subscriber it's ignored.
    """
    _bus.emit(EVENT_CREATED, _with_session(payload, session))


def emit_withdrawn(payload: dict[str, Any], *, session=None) -> None:
    """Emit ``complaint.withdrawn`` (payload: complaint_id, house_id, raised_by,
    reference). ``session`` threaded for atomic handler writes (see emit_created)."""
    _bus.emit(EVENT_WITHDRAWN, _with_session(payload, session))


def emit_status_changed(payload: dict[str, Any], *, session=None) -> None:
    """Emit ``complaint.status_changed`` (payload: complaint_id, house_id,
    raised_by, from_status, to_status, note, reference). ``session`` threaded for
    atomic handler writes (see emit_created)."""
    _bus.emit(EVENT_STATUS_CHANGED, _with_session(payload, session))


def mark_read_for(
    user_id: int, entity_type: str, entity_id: int, *, session=None
) -> None:
    """Clear-on-read hook (docs/05 §3): called when a user opens a complaint.

    Emits a ``complaint.mark_read`` event carrying the (user, entity) so
    Notifications can drop that user's pending alert for the entity. ``session``
    threaded so the clear happens in the opener's request transaction.
    """
    _bus.emit(
        EVENT_MARK_READ,
        _with_session(
            {
                "user_id": user_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
            },
            session,
        ),
    )


def _with_session(payload: dict[str, Any], session) -> dict[str, Any]:
    """Attach the emitter's request session to the event payload (subscriber uses
    it to write in the emitter's transaction). Returns the payload unchanged when
    no session is given (skeleton/back-compat)."""
    if session is not None:
        return {**payload, "session": session}
    return payload

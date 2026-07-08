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


def emit_created(payload: dict[str, Any]) -> None:
    """Emit ``complaint.created`` (payload: complaint_id, house_id, raised_by,
    reference, category_id)."""
    _bus.emit(EVENT_CREATED, payload)


def emit_withdrawn(payload: dict[str, Any]) -> None:
    """Emit ``complaint.withdrawn`` (payload: complaint_id, house_id, raised_by,
    reference)."""
    _bus.emit(EVENT_WITHDRAWN, payload)


def emit_status_changed(payload: dict[str, Any]) -> None:
    """Emit ``complaint.status_changed`` (payload: complaint_id, house_id,
    raised_by, from_status, to_status, note, reference)."""
    _bus.emit(EVENT_STATUS_CHANGED, payload)


def mark_read_for(user_id: int, entity_type: str, entity_id: int) -> None:
    """Clear-on-read hook (docs/05 §3): called when a user opens a complaint.

    Emits a ``complaint.mark_read`` event carrying the (user, entity) so
    Notifications can drop that user's pending alert for the entity. No-op until
    Notifications subscribes.
    """
    _bus.emit(
        EVENT_MARK_READ,
        {"user_id": user_id, "entity_type": entity_type, "entity_id": entity_id},
    )

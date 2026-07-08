"""Notice Board domain-event call surface (docs/modules/notice-board.md §7, docs/05 §3).

Thin wrappers the notices services call at the doc-specified sites. They route to
the shared in-process dispatcher (``app.common.events``); with Notifications not
yet built (it is Module 7, built AFTER Notice Board) there are no subscribers, so
every call is a safe no-op. When Notifications is built it ``subscribe``s to these
event names at startup — with ZERO change to the call sites here
(skeleton-then-wire; mirrors ``complaints/events.py``).

Event names + payloads are the contract in docs §7 / docs/05 §3:
- ``notice_posted`` -> Notifications delivers a ``notice`` alert to all current
  owners. Payload: ``notice_id``, ``society_id``, ``title``, ``published_at``.
- ``notice.mark_read`` -> clear-on-read: when an owner opens a notice, clear that
  owner's pending ``notice`` alert.
"""
from __future__ import annotations

from typing import Any

from app.common import events as _bus

# The publish event name is the cross-module contract (docs/05 §3 table). Kept as
# the doc's literal ``notice_posted`` (not namespaced) to match that table.
EVENT_POSTED = "notice_posted"
# The clear-on-read signal (Notifications subscribes; no-op until then).
EVENT_MARK_READ = "notice.mark_read"


def emit_posted(payload: dict[str, Any]) -> None:
    """Emit ``notice_posted`` on publish (payload: notice_id, society_id, title,
    published_at). Notifications fans out a ``notice`` alert to all current
    owners; no-op until it subscribes."""
    _bus.emit(EVENT_POSTED, payload)


def mark_read_for(user_id: int, entity_type: str, entity_id: int) -> None:
    """Clear-on-read hook (docs/05 §3): called when a user opens a notice.

    Emits a ``notice.mark_read`` event carrying the (user, entity) so
    Notifications can drop that user's pending ``notice`` alert for the notice.
    No-op until Notifications subscribes.
    """
    _bus.emit(
        EVENT_MARK_READ,
        {"user_id": user_id, "entity_type": entity_type, "entity_id": entity_id},
    )

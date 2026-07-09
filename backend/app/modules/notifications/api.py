"""Notifications public inter-module contract (docs/modules/notifications.md §7).

The surface OTHER modules / the app wiring import. Two categories:

- **Engine seams** (``notify`` / ``notify_many`` / ``clear_for_entity`` /
  ``unread_count``) — thin delegators over :class:`NotificationEngine` and the
  repository. Every caller passes the request-scoped ``Session`` so the write
  joins the caller's transaction (docs §4.1 — in-app rows commit atomically with
  the event that produced them).
- **Startup wiring** (``subscribe_handlers``) — called ONCE by ``create_app`` to
  register the event handlers on the in-process bus (docs §4.2). Idempotent.

Emitting modules NEVER import this — they ``emit`` to ``app.common.events`` and
Notifications subscribes here (skeleton-then-wire; the emitters don't change).
"""
from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.modules.notifications.services.engine import NotificationEngine
from app.modules.notifications.repository import NotificationRepository


def notify(
    session: Session,
    *,
    society_id: int,
    user_id: int,
    type: str,
    title: str,
    body: str,
    payload: dict[str, Any] | None = None,
    ref: tuple[str, int] | None = None,
    dedupe_key: str | None = None,
) -> int:
    """Create one in-app notification (idempotent on ``dedupe_key``) — docs §4.1.

    The single create choke point; the channel seam for future email/push.
    """
    return NotificationEngine(session).notify(
        society_id=society_id,
        user_id=user_id,
        type=type,
        title=title,
        body=body,
        payload=payload,
        ref=ref,
        dedupe_key=dedupe_key,
    )


def notify_many(
    session: Session,
    *,
    society_id: int,
    user_ids: Iterable[int],
    type: str,
    title: str,
    body: str,
    payload: dict[str, Any] | None = None,
    ref: tuple[str, int] | None = None,
    dedupe_key: str | None = None,
) -> int:
    """Fan out one notification to many recipients in a single batched insert
    (docs §4.1). Returns rows inserted."""
    return NotificationEngine(session).notify_many(
        society_id=society_id,
        user_ids=user_ids,
        type=type,
        title=title,
        body=body,
        payload=payload,
        ref=ref,
        dedupe_key=dedupe_key,
    )


def clear_for_entity(
    session: Session, *, user_id: int, entity_type: str, entity_id: int
) -> int:
    """Clear-on-read: drop a user's pending notifications for one entity
    (docs §4.4). Returns rows cleared."""
    return NotificationEngine(session).clear_for_entity(
        user_id=user_id, entity_type=entity_type, entity_id=entity_id
    )


def unread_count(session: Session, society_id: int, user_id: int) -> int:
    """The caller's unread badge count (docs §6/§7 — for the shell badge)."""
    return NotificationRepository(session).unread_count(society_id, user_id)


def subscribe_handlers() -> None:
    """Register every Notifications event handler on the in-process bus (docs §4.2).

    Called ONCE at app + worker startup. Idempotent per (event, handler) — the
    bus ``subscribe`` de-dupes — so a re-import/re-call does not double-fire.
    Delegates to the handlers module so the subscription list lives with the
    handler code.
    """
    from app.modules.notifications import handlers

    handlers.register_all()

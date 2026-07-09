"""The Notifications engine — the single choke point (docs/modules/notifications.md §4.1).

EVERY notification in the system is created here, and every clear-on-read passes
through here. This is deliberately the ONE seam so that:

- the **channel** stays swappable: today ``notify_many`` writes in-app rows; a
  future ``EmailSender``/``PushSender`` attaches here without touching a single
  caller (docs §1/§10 — the channel seam). The row's ``payload`` already carries
  what a push/WebSocket frame needs.
- **idempotency + batching** are guaranteed once: fan-outs are a single batched
  ``ON CONFLICT DO NOTHING`` insert (docs §3/§4), so a re-fire never double-posts
  and a broadcast to N recipients is one round trip.

Written and frozen in Phase A; event handlers, the dues rule, and the feed API
all build on this contract and must not re-implement creation.
"""
from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.common.time import utcnow
from app.modules.notifications.repository import NotificationRepository


class NotificationEngine:
    """The create + clear choke point over :class:`NotificationRepository`."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = NotificationRepository(session)

    # --- create ------------------------------------------------------------

    def notify(
        self,
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
        """Create ONE in-app notification for one recipient (docs §4.1).

        Idempotent on ``dedupe_key`` (insert-or-skip). Returns the number of rows
        inserted (1, or 0 if a dedupe_key collision skipped it). A thin wrapper
        over :meth:`notify_many` so there is exactly one write path.
        """
        return self.notify_many(
            society_id=society_id,
            user_ids=(user_id,),
            type=type,
            title=title,
            body=body,
            payload=payload,
            ref=ref,
            dedupe_key=dedupe_key,
        )

    def notify_many(
        self,
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
        """Fan out ONE notification (same content) to many recipients — one
        batched insert (§4.1).

        The scale + handler-failure-containment primitive (plan §7): a broadcast
        to N recipients is a single ``INSERT ... ON CONFLICT DO NOTHING`` (no
        N+1). Recipients are de-duplicated and NULL-filtered defensively so one
        bad id can't poison the batch. An empty recipient set is a safe no-op
        (returns 0). Returns the number of rows actually inserted.

        **Per-recipient dedupe.** ``(society_id, dedupe_key)`` is UNIQUE, so a
        single shared ``dedupe_key`` across a multi-recipient fan-out would
        collapse to ONE row. When ``dedupe_key`` is given here it is therefore
        made per-recipient by suffixing ``:{user_id}`` — so each recipient gets
        at most one row per logical fire (e.g. the dues rule keys
        ``dues:{house}:{day}`` and each owner is idempotent independently). Event
        fan-outs pass ``dedupe_key=None`` (they always insert).
        """
        entity_type, entity_id = (ref or (None, None))
        clean_ids = _clean_recipient_ids(user_ids)
        if not clean_ids:
            return 0
        rows = [
            {
                "society_id": society_id,
                "user_id": uid,
                "type": type,
                "title": title,
                "body": body,
                "payload": payload or {},
                "entity_type": entity_type,
                "entity_id": entity_id,
                "dedupe_key": (
                    f"{dedupe_key}:{uid}" if dedupe_key is not None else None
                ),
                "read_at": None,
            }
            for uid in clean_ids
        ]
        return self._repo.insert_many(rows)

    # --- clear-on-read -----------------------------------------------------

    def clear_for_entity(
        self, *, user_id: int, entity_type: str, entity_id: int
    ) -> int:
        """Clear a user's pending notifications for one entity (docs §4.4).

        The core of the ``mark_read_for`` hook: when the user opens the
        underlying item (a complaint / a notice), drop their pending alert(s) for
        it. Sets ``read_at`` on matching unread rows. Returns rows cleared (0 is a
        safe no-op — nothing was pending).
        """
        return self._repo.mark_entity_read(
            user_id, entity_type, entity_id, now=utcnow()
        )


# --- module-level helpers -----------------------------------------------------


def _clean_recipient_ids(user_ids: Iterable[int]) -> list[int]:
    """De-dupe + drop NULLs from a recipient set (defensive, order-stable).

    One malformed/None recipient must never poison a whole batched fan-out
    (plan §7 — handler-failure containment). Order is preserved for deterministic
    tests.
    """
    seen: set[int] = set()
    out: list[int] = []
    for uid in user_ids:
        if uid is None:
            continue
        uid = int(uid)
        if uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out

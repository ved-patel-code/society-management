"""Notifications event subscriptions (docs/modules/notifications.md §4.2).

The wiring seam: at app + worker startup ``register_all()`` subscribes one handler
per domain event on the in-process bus (``app.common.events``). Emitting modules
(Complaints, Notice Board) already ``emit`` these events with no subscribers today
(skeleton-then-wire) — registering here turns those dormant emits into real
notifications with ZERO change to the emitters (docs §7).

Handler contract (docs §4.1/§4.2, plan §7 — handler-failure containment). This
is the FROZEN design the wave agents implement against:

1. **Own short-lived session.** The bus is frozen at ``emit(event, payload)`` and
   passes ONLY a data payload — no session, no ambient context. So each handler
   opens its OWN ``SessionLocal`` session, does its writes, and commits/rolls back
   independently, then closes. This keeps the emitters and the bus completely
   unchanged (true skeleton-then-wire — the emitters were shipped promising zero
   call-site edits). Trade-off vs. the doc's "same transaction" wording: a
   notification is its own unit of work, so a crash between the source commit and
   the handler commit leaves a bounded, LOGGED, non-cascading gap — acceptable
   because (a) the bus already decouples handler success from the emitter, and (b)
   every write is idempotent (``dedupe_key``/``ON CONFLICT``) so any retry/re-run
   is exactly-once. Documented as a deviation in ``docs/implemented``.
2. **Work from the payload, never re-query the source row.** Because the handler
   runs in a DIFFERENT session while the emitter's transaction may still be open,
   the just-created complaint/notice row is NOT yet visible to the handler. Every
   field a handler needs about the source entity (ids, reference, statuses, title)
   is ALREADY in the event payload — handlers use it directly. Recipient
   resolution reads OTHER, already-committed tables (roles/permissions,
   occupancy), which is safe.
3. **Never raise out of the bus, and contain the blast radius.** The bus
   logs+swallows, but handlers ALSO guard defensively so one bad society/recipient
   can't drop alerts for others (plan §7): resolve recipients defensively, insert
   via the batched ``ON CONFLICT`` primitive, log failures with event + society.

WAVE W1/W2 fill the handler bodies + the subscription list here (editing only
this file + the handler concern files + their tests). Phase A ships the frozen
``register_all`` seam so ``create_app`` can call it today (idempotent no-op until
handlers are registered).
"""
from __future__ import annotations

import logging

logger = logging.getLogger("app.notifications.handlers")

# Set once ``register_all`` has run, so a re-call is a cheap no-op on top of the
# bus's own per-(event, handler) idempotency.
_REGISTERED = False


def register_all() -> None:
    """Subscribe every Notifications handler on the in-process bus (docs §4.2).

    Idempotent: safe to call at both app and worker startup and safe to re-call
    (the bus de-dupes per (event, handler); this flag avoids the re-import work).

    FROZEN SEAM (Phase A): the body wires the event → handler subscriptions once
    Wave W1 (event handlers) and Wave W2 (mark-read handlers) land. Until then it
    is a safe no-op so the app boots with the module registered but no live
    subscriptions.
    """
    global _REGISTERED
    if _REGISTERED:
        return

    from app.common import events
    from app.modules.notifications.services import event_handlers as eh

    # Event-driven notification handlers (docs §4.2).
    events.subscribe("complaint.created", eh.on_complaint_created)
    events.subscribe("complaint.withdrawn", eh.on_complaint_withdrawn)
    events.subscribe("complaint.status_changed", eh.on_complaint_status_changed)
    events.subscribe("notice_posted", eh.on_notice_posted)

    # Clear-on-read (mark_read) handlers — same handler for both entities; it
    # keys purely off the payload's (user_id, entity_type, entity_id).
    events.subscribe("complaint.mark_read", eh.on_mark_read)
    events.subscribe("notice.mark_read", eh.on_mark_read)

    _REGISTERED = True
    logger.info("Notifications handlers registered.")


def _reset_for_tests() -> None:
    """Test-only: clear the registered flag so a test can re-subscribe against a
    freshly-``clear()``ed bus (isolation)."""
    global _REGISTERED
    _REGISTERED = False

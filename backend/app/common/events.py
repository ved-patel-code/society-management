"""A lightweight in-process domain-event dispatcher (docs/05 §3).

The "when X happens, notify/act" seam. A module emits a domain event
(``emit("complaint.created", payload)``); interested modules subscribe a handler
(``subscribe("complaint.created", fn)``). This keeps notification/reaction logic
OUT of the emitting module's core — the emitter never imports Notifications, and
Notifications never edits the emitter.

**Synchronous + in-transaction.** Handlers run inline, in the emitter's request
transaction (or worker session), so a handler that writes rows commits/rolls back
atomically with the state change that triggered it. This is intentional for v1:
in-app notifications are cheap DB writes and belong in the same unit of work as
the event. (Anything genuinely slow must go to the worker, not a handler.)

**Emitting is safe with zero subscribers** — ``emit`` is a no-op then. This is
exactly the Complaints-before-Notifications situation: Complaints emits today, and
when Notifications is built it calls ``subscribe(...)`` at startup with no change
to Complaints' call sites.

**A handler must never break the emitter.** A handler exception is logged and
swallowed so a subscriber bug can't fail the user's action (raising a complaint
must still succeed if a notification handler throws). Handlers that need
transactional coupling with the emitter should be written not to raise; a raised
exception is treated as a subscriber-side defect, not a signal to abort the
emitter.

Registration is process-global and idempotent per (event, handler) — importing a
subscriber module twice registers its handler once. The registry is module-level
state seeded at import/startup; it is not per-request.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("app.events")

# event name -> ordered list of handlers. Handlers are called in registration
# order. Each handler takes the payload dict and returns None.
Handler = Callable[[dict[str, Any]], None]
_SUBSCRIBERS: dict[str, list[Handler]] = {}


def subscribe(event: str, handler: Handler) -> None:
    """Register ``handler`` for ``event`` (idempotent per (event, handler)).

    Called once at startup by a subscribing module (e.g. Notifications registers
    its handlers for ``complaint.*``). Re-registering the same callable is a no-op
    so a re-imported subscriber module does not double-fire.
    """
    handlers = _SUBSCRIBERS.setdefault(event, [])
    if handler not in handlers:
        handlers.append(handler)


def unsubscribe(event: str, handler: Handler) -> None:
    """Remove a previously-registered handler (used by tests for isolation)."""
    handlers = _SUBSCRIBERS.get(event)
    if handlers and handler in handlers:
        handlers.remove(handler)


def clear(event: str | None = None) -> None:
    """Drop all handlers for ``event`` (or every event when ``None``).

    Test-support only — production code subscribes at startup and never clears.
    """
    if event is None:
        _SUBSCRIBERS.clear()
    else:
        _SUBSCRIBERS.pop(event, None)


def emit(event: str, payload: dict[str, Any]) -> None:
    """Fire ``event`` to every subscribed handler, in registration order.

    A no-op if nothing is subscribed (the emitter never needs to know whether a
    consumer exists). A handler exception is logged and swallowed so one bad
    subscriber can never break the action that emitted the event.
    """
    handlers = _SUBSCRIBERS.get(event)
    if not handlers:
        return
    for handler in handlers:
        try:
            handler(payload)
        except Exception:  # pragma: no cover - defensive; a subscriber bug
            logger.exception(
                "event handler failed for %s (payload keys=%s)",
                event,
                sorted(payload.keys()),
            )

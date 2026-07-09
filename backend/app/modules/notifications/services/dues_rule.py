"""The maintenance-dues reminder rule (docs/modules/notifications.md §4.3).

The v1 scheduled reminder: a stateless, idempotent cadence that fires a single
CONSOLIDATED ``maintenance_due`` notification per owing house, to that house's
current owners, on the right days — and stops automatically once the house is
paid. Consumes Finance (``outstanding_dues``) + the society's
``maintenance_due_day``; the cadence knobs (X advance / N interval) live in
Notifications config (docs §4.3/§8), NOT Finance.

Cadence (per owing house), anchored on the MOST RECENT outstanding ``due_date``:
- ``today == due_date - X``            → advance heads-up      (X = dues_advance_days)
- ``today == due_date``                → due-day reminder
- ``today > due_date`` and
  ``(today - due_date) % N == 0``      → recurring nag         (N = interval)
while any balance remains. Idempotent via ``dedupe_key = dues:{house_id}:{today}``
(made per-recipient by the engine) → at most one dues reminder per house per day;
the cadence is computed from dates + config, so NO "last-fired" state is stored. A
later fire is a NEW notification even if the earlier one was read while unpaid.

This module is pure logic (``is_fire_day`` + ``build``): the worker (``jobs.py``)
drives it per society. Kept side-effect-free except the final engine call so it is
trivially unit-testable against crafted dates.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.modules.finance import api as finance_api
from app.modules.houses.service import HouseService
from app.modules.notifications.schemas import (
    ENTITY_HOUSE,
    NotificationsConfig,
    TYPE_MAINTENANCE_DUE,
)
from app.modules.notifications.services.engine import NotificationEngine

REMINDER_KEY = "dues"


def is_fire_day(anchor_due_date: date, cfg: NotificationsConfig, today: date) -> bool:
    """Whether today is a reminder day for a house anchored on ``anchor_due_date``.

    Pure function of dates + config (docs §4.3) — no I/O, no state. See the
    module cadence spec. ``anchor_due_date`` is the house's MOST RECENT
    outstanding due date.
    """
    delta = (today - anchor_due_date).days
    if delta == -cfg.dues_advance_days:
        return True  # advance heads-up (X days before)
    if delta == 0:
        return True  # due-day
    if delta > 0 and delta % cfg.dues_reminder_interval_days == 0:
        return True  # recurring nag every N days while unpaid
    return False


def _anchor_due_date(outstanding: list[Any]) -> date | None:
    """The most recent outstanding ``due_date`` — the cadence anchor (docs §4.3)."""
    if not outstanding:
        return None
    return max(line.due_date for line in outstanding)


def build_for_house(
    session: Session,
    *,
    society_id: int,
    house_id: int,
    cfg: NotificationsConfig,
    today: date,
    owners: set[int] | None = None,
) -> int:
    """Evaluate the rule for one house and, if today fires, create the reminder.

    Reads the house's outstanding dues via the Finance interface (never its
    tables). If there's no balance, nothing is built (the reminder auto-stops when
    paid). If today is a fire day, builds ONE consolidated ``maintenance_due``
    notification (total = Σ all unpaid months) for each current owner of the
    house, idempotent per (house, day). Returns rows inserted (0 when not a fire
    day / paid / no reachable owner).

    ``owners`` may be pre-resolved by the caller (the worker batches owner lookup
    across all owing houses in ONE query — no N+1); when omitted it is resolved
    per house (the on-demand/single-house path).
    """
    dues = finance_api.outstanding_dues(session, society_id, house_id)
    outstanding = list(dues.outstanding)
    if not outstanding:
        return 0

    anchor = _anchor_due_date(outstanding)
    if anchor is None or not is_fire_day(anchor, cfg, today):
        return 0

    if owners is None:
        owners = HouseService(session).owner_user_ids_for_house(
            society_id, house_id
        )
    if not owners:
        return 0

    total: Decimal = dues.outstanding_total
    months = len(outstanding)
    return NotificationEngine(session).notify_many(
        society_id=society_id,
        user_ids=owners,
        type=TYPE_MAINTENANCE_DUE,
        title="Maintenance dues pending",
        body=(
            f"You have {months} month(s) of maintenance dues outstanding, "
            f"totalling {total}."
        ),
        payload={
            "house_id": house_id,
            "outstanding_total": str(total),
            "months_outstanding": months,
            "anchor_due_date": anchor.isoformat(),
        },
        ref=(ENTITY_HOUSE, house_id),
        # Per-house-per-day idempotency; the engine suffixes :{user_id} so each
        # owner is deduped independently (docs §4.3).
        dedupe_key=f"{REMINDER_KEY}:{house_id}:{today.isoformat()}",
    )

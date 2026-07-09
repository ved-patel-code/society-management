"""Shared Notifications service internals (docs/modules/notifications.md §8).

Small, dependency-light helpers every concern reuses so logic lives in ONE place
(docs/03 §1): resolve the validated per-society config and partial-merge writes
to ``society_modules.config``. Mirrors Finance/Complaints ``support`` +
``config_svc`` split.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.errors import ValidationError
from app.modules.notifications.schemas import CONFIG_KEYS, NotificationsConfig
from app.platform.models import SocietyModule

MODULE_KEY = "notifications"


def _society_module(session: Session, society_id: int) -> SocietyModule | None:
    return session.execute(
        select(SocietyModule).where(
            SocietyModule.society_id == society_id,
            SocietyModule.module_key == MODULE_KEY,
        )
    ).scalar_one_or_none()


def load_config(session: Session, society_id: int) -> NotificationsConfig:
    """The validated notifications config for a society (docs §8).

    Reads ``society_modules.config`` for the notifications module and validates
    it through :class:`NotificationsConfig` (defaults fill any missing key). Only
    this module's keys are pulled; unrelated config is ignored.
    """
    module = _society_module(session, society_id)
    raw = (module.config or {}) if module is not None else {}
    data = {k: raw[k] for k in CONFIG_KEYS if k in raw}
    return NotificationsConfig(**data)


def write_config(
    session: Session, society_id: int, changes: dict[str, int]
) -> NotificationsConfig:
    """PARTIAL-MERGE the given keys into the society's notifications config (§8).

    Only whitelisted keys present in ``changes`` are written; every other key in
    ``society_modules.config`` is preserved. Returns the resulting validated
    config. Raises if the module row is absent (module not enabled) — the route
    is module-gated, so this is a defensive guard.
    """
    module = _society_module(session, society_id)
    if module is None:
        raise ValidationError(
            "Notifications module is not enabled for this society.",
            details={"module_key": MODULE_KEY},
        )
    merged = dict(module.config or {})
    for key in CONFIG_KEYS:
        if key in changes and changes[key] is not None:
            merged[key] = changes[key]
    # Validate the merged result before persisting (defence in depth).
    validated = NotificationsConfig(
        **{k: merged[k] for k in CONFIG_KEYS if k in merged}
    )
    # Reassign (not in-place mutate) so SQLAlchemy tracks the JSON change.
    module.config = merged
    session.add(module)
    session.flush()
    return validated

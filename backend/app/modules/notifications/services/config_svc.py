"""Notifications config concern (docs/modules/notifications.md §5/§6/§8).

Owns ``GET /notifications/config`` (read via ``support.load_config``) and
``PUT /notifications/config`` (PARTIAL MERGE via ``support.write_config`` — only
provided keys change). Audits ``notifications.config_updated`` with before/after
(the ONLY audited action in this module — docs §5). Mirrors the
Complaints/Finance config concern.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.common.errors import ValidationError
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.schemas import (
    CONFIG_KEYS,
    ConfigOut,
    ConfigUpdateRequest,
    NotificationsConfig,
)
from app.modules.notifications.services import support
from app.platform.audit.service import AuditService


class ConfigService:
    def __init__(self, session: Session, repo: NotificationRepository) -> None:
        self._session = session
        self._repo = repo

    def get_config(self, society_id: int) -> ConfigOut:
        """Read the society's notifications config (§6/§8). No write, no audit."""
        return self._to_out(support.load_config(self._session, society_id))

    def update_config(
        self, society_id: int, req: ConfigUpdateRequest, *, actor_user_id: int
    ) -> ConfigOut:
        """Partial-merge update the config; audits before/after (§5/§6/§8).

        Only fields the caller actually provided (non-``None``) change; every
        unspecified key keeps its current value. An empty request (no field set)
        is a 422 — nothing to update. Writes a ``notifications.config_updated``
        audit row with the full before/after config.
        """
        changes = {
            key: value
            for key in CONFIG_KEYS
            if (value := getattr(req, key)) is not None
        }
        if not changes:
            raise ValidationError(
                "Provide at least one config field to update.",
                details={"fields": list(CONFIG_KEYS)},
            )

        before = support.load_config(self._session, society_id)
        after = support.write_config(self._session, society_id, changes)

        AuditService(self._session).record(
            action="notifications.config_updated",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="society_module",
            entity_id=society_id,
            before=self._as_dict(before),
            after=self._as_dict(after),
        )
        return self._to_out(after)

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _as_dict(cfg: NotificationsConfig) -> dict[str, int]:
        return {key: getattr(cfg, key) for key in CONFIG_KEYS}

    @staticmethod
    def _to_out(cfg: NotificationsConfig) -> ConfigOut:
        return ConfigOut(
            dues_advance_days=cfg.dues_advance_days,
            dues_reminder_interval_days=cfg.dues_reminder_interval_days,
            read_retention_days=cfg.read_retention_days,
        )

"""Complaints config concern — WAVE E (docs/modules/complaints.md §4/§6/§8).

Owns ``GET /complaints/config`` (read via ``support.load_config``) and
``PUT /complaints/config`` (PARTIAL MERGE via ``support.write_config`` — only
provided keys change; unspecified keys keep their current value). Audits
``complaints.config_updated`` with before/after. The read/write helpers already
live in ``support.py``; this concern is the thin service + audit wrapper.

FROZEN STUBS: Wave E fills the bodies, editing only THIS file + its own test file.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.common.errors import ValidationError
from app.modules.complaints.repository import ComplaintRepository
from app.modules.complaints.schemas import (
    ComplaintsConfig,
    ComplaintsConfigOut,
    ConfigUpdateRequest,
)
from app.modules.complaints.services import support
from app.platform.audit.service import AuditService

# The config keys this concern reads/writes/audits — the whole module surface
# (docs §8). Kept in step with ``support._CONFIG_KEYS`` (the merge whitelist).
_CONFIG_KEYS = ("auto_archive_days", "max_report_images", "max_proof_images")


class ConfigService:
    def __init__(self, session: Session, repo: ComplaintRepository) -> None:
        self._session = session
        self._repo = repo

    def get_config(self, society_id: int) -> ComplaintsConfigOut:
        """Read the society's complaints config (§6/§8).

        Loads (and defaults) the validated config via ``support.load_config`` and
        projects it to the response shape. No write, no audit.
        """
        cfg = support.load_config(self._session, society_id)
        return self._to_out(cfg)

    def update_config(
        self, society_id: int, req: ConfigUpdateRequest, *, actor_user_id: int
    ) -> ComplaintsConfigOut:
        """Partial-merge update the config; audits before/after (§6/§8).

        Only the fields the caller actually provided (non-``None``) are changed;
        every unspecified key keeps its current value (the merge lives in
        ``support.write_config``). An empty request (no field set) is a 422 — there
        is nothing to update. Writes a ``complaints.config_updated`` audit row with
        the full before/after config so the change is reconstructable.
        """
        # Only the provided (non-None) fields form the change set. Bounds were
        # already enforced by the request schema.
        changes = {
            key: value
            for key in _CONFIG_KEYS
            if (value := getattr(req, key)) is not None
        }
        if not changes:
            raise ValidationError(
                "Provide at least one config field to update.",
                details={"fields": list(_CONFIG_KEYS)},
            )

        before = support.load_config(self._session, society_id)
        after = support.write_config(self._session, society_id, changes)

        AuditService(self._session).record(
            action="complaints.config_updated",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="society_module",
            entity_id=society_id,
            before=self._as_dict(before),
            after=self._as_dict(after),
        )
        return self._to_out(after)

    # --- helpers -------------------------------------------------------------

    @staticmethod
    def _as_dict(cfg: ComplaintsConfig) -> dict[str, int]:
        """The three config keys as a plain dict (audit before/after payload)."""
        return {key: getattr(cfg, key) for key in _CONFIG_KEYS}

    @staticmethod
    def _to_out(cfg: ComplaintsConfig) -> ComplaintsConfigOut:
        return ComplaintsConfigOut(
            auto_archive_days=cfg.auto_archive_days,
            max_report_images=cfg.max_report_images,
            max_proof_images=cfg.max_proof_images,
        )

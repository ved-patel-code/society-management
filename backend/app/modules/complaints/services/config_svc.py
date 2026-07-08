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

from app.modules.complaints.repository import ComplaintRepository
from app.modules.complaints.schemas import ComplaintsConfigOut, ConfigUpdateRequest


class ConfigService:
    def __init__(self, session: Session, repo: ComplaintRepository) -> None:
        self._session = session
        self._repo = repo

    def get_config(self, society_id: int) -> ComplaintsConfigOut:
        """Read the society's complaints config (§6/§8)."""
        raise NotImplementedError("Wave E: ConfigService.get_config")

    def update_config(
        self, society_id: int, req: ConfigUpdateRequest, *, actor_user_id: int
    ) -> ComplaintsConfigOut:
        """Partial-merge update the config; audits before/after (§6/§8)."""
        raise NotImplementedError("Wave E: ConfigService.update_config")

"""SocietyService — super-admin society lifecycle + module allocation.

All business logic for the societies package lives here (docs/03 §2): create a
society (hash the required default member password, then instantiate its roles by
copy — docs/PF §14.1), read/update config, and allocate/toggle modules enforcing
``depends_on`` (docs/PF §6). Every state change writes an audit row in the SAME
session; the service never commits (``get_session`` commits once — docs/PF §12).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.common.errors import NotFoundError, ValidationError
from app.common.time import utcnow
from app.common.validators import validate_password_policy
from app.core.registry import MODULE_REGISTRY
from app.core.security import hash_password
from app.platform.audit.service import AuditService
from app.platform.models import Society, SocietyModule
from app.platform.roles.service import RoleService
from app.platform.societies.repository import SocietyRepository
from app.platform.societies.schemas import (
    SOCIETY_STATUSES,
    ModuleAllocation,
    SocietyCreate,
    SocietyUpdate,
)

# Fields whose before/after we snapshot for the society.updated audit diff.
_UPDATABLE_FIELDS: tuple[str, ...] = (
    "name",
    "storage_limit_bytes",
    "currency",
    "timezone",
    "settings",
    "status",
)


class SocietyService:
    """Orchestrates society creation, config updates, and module allocation."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = SocietyRepository(session)
        self._audit = AuditService(session)

    # --- create ------------------------------------------------------------

    def create_society(self, data: SocietyCreate, *, actor_user_id: int) -> Society:
        """Create a society (``type`` NULL, status ``onboarding``) then copy in its
        default roles (docs/PF §3/§6/§14.1).

        The required default member password is policy-checked and stored hashed
        (Argon2id) — plaintext is never persisted (docs/PF §14.6).
        """
        validate_password_policy(data.default_member_password)

        society = Society(
            name=data.name,
            type=None,  # society_admin picks building vs individual in onboarding
            status="onboarding",
            storage_limit_bytes=data.storage_limit_bytes,
            default_member_password_hash=hash_password(data.default_member_password),
            currency=data.currency,
            timezone=data.timezone,
            settings=data.settings,
        )
        self._repo.add(society)  # flush → society.id assigned within this txn

        # Roles-by-copy (docs/PF §14.1) — consumes P2's RoleService (contract).
        RoleService(self._session).instantiate_society_roles(
            society.id, actor_user_id=actor_user_id
        )

        self._audit.record(
            action="society.created",
            actor_user_id=actor_user_id,
            society_id=society.id,
            entity_type="society",
            entity_id=society.id,
            after=self._society_snapshot(society),
        )
        return society

    # --- read --------------------------------------------------------------

    def list_societies(
        self, *, limit: int, offset: int
    ) -> tuple[list[Society], int]:
        return self._repo.list_page(limit=limit, offset=offset)

    def get_society(self, society_id: int) -> Society:
        society = self._repo.get(society_id)
        if society is None:
            raise NotFoundError(
                "Society not found.", details={"society_id": society_id}
            )
        return society

    # --- update ------------------------------------------------------------

    def update_society(
        self, society_id: int, data: SocietyUpdate, *, actor_user_id: int
    ) -> Society:
        """Patch mutable config; audit a before/after diff of changed fields.

        Only provided fields are applied. ``status`` is validated against the
        allowed domain (docs/PF §3).
        """
        society = self.get_society(society_id)
        changes = data.model_dump(exclude_unset=True)

        if "status" in changes and changes["status"] not in SOCIETY_STATUSES:
            raise ValidationError(
                "Invalid society status.",
                details={
                    "field": "status",
                    "allowed": sorted(SOCIETY_STATUSES),
                },
            )

        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        for field in _UPDATABLE_FIELDS:
            if field not in changes:
                continue
            new_value = changes[field]
            old_value = getattr(society, field)
            if new_value == old_value:
                continue
            before[field] = old_value
            after[field] = new_value
            setattr(society, field, new_value)

        if after:
            self._session.flush()
            self._audit.record(
                action="society.updated",
                actor_user_id=actor_user_id,
                society_id=society.id,
                entity_type="society",
                entity_id=society.id,
                before=before,
                after=after,
            )
        return society

    # --- module allocation -------------------------------------------------

    def set_modules(
        self,
        society_id: int,
        allocations: list[ModuleAllocation],
        *,
        actor_user_id: int,
    ) -> list[SocietyModule]:
        """Allocate/toggle modules for a society, enforcing ``depends_on``.

        For each enable, dependencies are resolved against the society's CURRENT
        plus in-request enabled set (docs/PF §6) via the module registry — which
        also rejects unknown module keys (foundation has only ``platform``). Rows
        are upserted (``enabled_by``/``enabled_at`` stamped on enable). Duplicate
        keys within one request are rejected up front.
        """
        society = self.get_society(society_id)

        seen: set[str] = set()
        for alloc in allocations:
            if alloc.module_key in seen:
                raise ValidationError(
                    "Duplicate module_key in request.",
                    details={"module_key": alloc.module_key},
                )
            seen.add(alloc.module_key)

        existing = {m.module_key: m for m in self._repo.list_modules(society_id)}
        enabled_keys = {k for k, m in existing.items() if m.enabled}

        role_service = RoleService(self._session)

        results: list[SocietyModule] = []
        for alloc in allocations:
            if alloc.enabled:
                # depends_on check uses the set excluding this key itself.
                MODULE_REGISTRY.resolve_dependencies(
                    alloc.module_key, enabled_keys - {alloc.module_key}
                )

            row = existing.get(alloc.module_key)
            is_new = row is None
            before = None if is_new else self._module_snapshot(row)

            if row is None:
                row = SocietyModule(
                    society_id=society_id,
                    module_key=alloc.module_key,
                    enabled=alloc.enabled,
                    config=alloc.config,
                )
                if alloc.enabled:
                    row.enabled_by = actor_user_id
                    row.enabled_at = utcnow()
                self._repo.add_module(row)
                existing[alloc.module_key] = row
            else:
                # No-op: nothing to change → skip the write AND the audit so an
                # idempotent re-POST doesn't spam module.toggled rows (docs/PF §12).
                if row.enabled == alloc.enabled and row.config == alloc.config:
                    if alloc.enabled:
                        enabled_keys.add(alloc.module_key)
                    else:
                        enabled_keys.discard(alloc.module_key)
                    results.append(row)
                    continue

                turning_on = alloc.enabled and not row.enabled
                row.enabled = alloc.enabled
                row.config = alloc.config
                if turning_on:
                    row.enabled_by = actor_user_id
                    row.enabled_at = utcnow()
                self._session.flush()

            # Keep the running enabled set coherent for later deps in this batch.
            if alloc.enabled:
                enabled_keys.add(alloc.module_key)
            else:
                enabled_keys.discard(alloc.module_key)

            self._audit.record(
                action="module.allocated" if is_new else "module.toggled",
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type="society_module",
                entity_id=row.id,
                before=before,
                after=self._module_snapshot(row),
            )
            results.append(row)

        # Grant each ENABLED module's default role→permission set to the society's
        # matching roles (docs/PF §5; each module doc's "Default seeding" line).
        # Idempotent, so it also self-heals a society that pre-dated the module's
        # defaults. Only modules that are enabled in the FINAL state are granted.
        final_enabled = {m.module_key for m in existing.values() if m.enabled}
        for module_key in final_enabled:
            spec = MODULE_REGISTRY.get(module_key)
            if spec is not None and spec.default_role_permissions:
                role_service.grant_default_module_permissions(
                    society_id,
                    spec.default_role_permissions,
                    actor_user_id=actor_user_id,
                )

        # Return the full, ordered module set so the caller sees final state.
        return self._repo.list_modules(society.id)

    # --- audit snapshots ---------------------------------------------------

    @staticmethod
    def _society_snapshot(society: Society) -> dict[str, Any]:
        return {
            "name": society.name,
            "type": society.type,
            "status": society.status,
            "storage_limit_bytes": society.storage_limit_bytes,
            "currency": society.currency,
            "timezone": society.timezone,
            "settings": society.settings,
        }

    @staticmethod
    def _module_snapshot(module: SocietyModule) -> dict[str, Any]:
        return {
            "module_key": module.module_key,
            "enabled": module.enabled,
            "config": module.config,
        }

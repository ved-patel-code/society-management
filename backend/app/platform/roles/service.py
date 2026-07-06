"""RoleService — roles/permissions business logic (docs/PF §5, §5.1, §14.1).

The brain for the roles feature: it copies templates into society-scoped rows on
society creation, computes effective permissions (the union across a user's
roles), derives view-only portals, resolves portal-scoped visible modules, and
backs the two super-admin endpoints. All queries go through
:class:`RoleRepository`; every state change is audited in the same session and
NEVER committed here (``get_session`` commits once at request end — docs/PF §12).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.common.errors import ConflictError, NotFoundError, ValidationError
from app.platform.audit.service import AuditService
from app.platform.bootstrap import SOCIETY_DEFAULT_ROLE_KEYS
from app.platform.models import Role
from app.platform.roles.repository import RoleRepository


class RoleService:
    """Data-driven role management (see the wave-1 contract for signatures)."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = RoleRepository(session)
        self._audit = AuditService(session)

    # --- society bootstrap: roles by copy (docs/PF §5/§14.1) -------------

    def instantiate_society_roles(
        self, society_id: int, *, actor_user_id: int | None
    ) -> dict[str, int]:
        """Copy the global society-scoped templates (``society_admin``,
        ``resident``) into society-scoped rows for ``society_id``, INCLUDING their
        ``role_permissions`` and ``role_module_visibility``. ``super_admin`` stays
        global and is never copied.

        Idempotent: keys already present for the society are skipped. Returns
        ``{role_key: role_id}`` for every default role now present (created or
        pre-existing). Writes ``role.created`` per newly created role.
        """
        existing_keys = self._repo.society_role_keys(society_id)
        templates = {
            t.key: t
            for t in self._repo.global_templates_by_keys(SOCIETY_DEFAULT_ROLE_KEYS)
        }

        result: dict[str, int] = {}
        for key in SOCIETY_DEFAULT_ROLE_KEYS:
            if key in existing_keys:
                # Already instantiated for this society — surface its id, skip copy.
                existing = self._repo.society_role_by_key(society_id, key)
                if existing is not None:
                    result[key] = existing.id
                continue

            template = templates.get(key)
            if template is None:
                # Templates are seeded before any society is created; a missing one
                # means the platform seed did not run (docs/PF §2 step 1).
                raise NotFoundError(
                    f"Global role template '{key}' is not seeded.",
                    details={"role_key": key},
                )

            role = self._repo.add_role(
                Role(
                    society_id=society_id,
                    key=template.key,
                    name=template.name,
                    is_system=template.is_system,
                    scope=template.scope,
                    portal=template.portal,
                )
            )

            # Copy the template's permission set (empty in the foundation — that is
            # correct and fine; the structure copies whatever the template holds).
            template_perm_ids = self._repo.role_permission_ids(template.id)
            if template_perm_ids:
                self._repo.add_role_permissions(role.id, template_perm_ids)

            # Copy the template's tab-visibility rows so the new portal renders.
            self._repo.copy_role_module_visibility(template.id, role.id)

            self._audit.record(
                action="role.created",
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type="role",
                entity_id=role.id,
                after={
                    "key": role.key,
                    "name": role.name,
                    "scope": role.scope,
                    "portal": role.portal,
                    "source": "template_copy",
                },
            )
            result[key] = role.id

        return result

    # --- default module permissions on enable (docs/PF §5; module docs) ---

    def grant_default_module_permissions(
        self,
        society_id: int,
        role_permissions: dict[str, list[str]],
        *,
        actor_user_id: int | None,
    ) -> None:
        """ADDITIVELY grant a module's default permissions to a society's roles.

        Called when a module is enabled for a society (each module doc's "Default
        seeding (data-driven roles)" line — e.g. onboarding grants
        ``society_admin`` → ``onboarding.manage``/``onboarding.read``). Idempotent:
        only permissions the role does not already hold are added, so re-enabling a
        module never duplicates rows or re-audits. Unknown permission keys and
        absent roles are skipped silently (the module may target roles a given
        society hasn't created). Audits ``permission.granted_by_module`` per role
        that actually gains keys.
        """
        for role_key, perm_keys in role_permissions.items():
            if not perm_keys:
                continue
            role = self._repo.society_role_by_key(society_id, role_key)
            if role is None:
                continue

            existing = self._repo.role_permission_keys(role.id)
            missing_keys = [k for k in perm_keys if k not in existing]
            if not missing_keys:
                continue

            found = self._repo.permission_ids_for_keys(missing_keys)
            new_ids = [found[k] for k in missing_keys if k in found]
            if not new_ids:
                continue

            self._repo.add_role_permissions(role.id, new_ids)
            self._audit.record(
                action="permission.granted_by_module",
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type="role",
                entity_id=role.id,
                after={
                    "role_key": role_key,
                    "granted_permission_keys": sorted(
                        k for k in missing_keys if k in found
                    ),
                },
            )

    # --- effective permissions / portals / modules (docs/PF §5/§5.1) -----

    def effective_permission_keys(
        self, user_id: int, society_id: int | None
    ) -> set[str]:
        """Union of permission keys across the user's roles in the society."""
        if society_id is None:
            return set()
        return self._repo.effective_permission_keys(user_id, society_id)

    def available_portals(
        self, user_id: int, society_id: int | None
    ) -> list[str]:
        """Distinct ``roles.portal`` across the user's roles in the society
        (view-only; drives the login portal chooser — docs/PF §5.1)."""
        if society_id is None:
            return []
        return self._repo.user_portals(user_id, society_id)

    def visible_modules_for_portal(
        self, user_id: int, society_id: int, portal: str
    ) -> list[str]:
        """Enabled-and-visible module keys for ``portal``: the intersection of the
        user's roles' ``role_module_visibility`` (for roles in that portal) with
        the society's ENABLED ``society_modules`` (docs/PF §5.1)."""
        return self._repo.visible_module_keys_for_portal(
            user_id, society_id, portal
        )

    # --- super-admin endpoints (docs/PF §10) -----------------------------

    def create_role(
        self,
        *,
        society_id: int,
        key: str,
        name: str,
        portal: str,
        scope: str,
        permission_keys: list[str],
        actor_user_id: int | None,
    ) -> Role:
        """Create a society-scoped custom role with an optional permission set.

        Enforces per-society key uniqueness in the service (the DB UNIQUE on
        ``(society_id, key)`` is the concurrency safety net). Audits
        ``role.created``.
        """
        if self._repo.society_role_by_key(society_id, key) is not None:
            raise ConflictError(
                f"A role with key '{key}' already exists for this society.",
                details={"society_id": society_id, "role_key": key},
            )

        permission_ids = self._resolve_permission_ids(permission_keys)

        role = self._repo.add_role(
            Role(
                society_id=society_id,
                key=key,
                name=name,
                is_system=False,  # custom roles are never system roles
                scope=scope,
                portal=portal,
            )
        )
        if permission_ids:
            self._repo.add_role_permissions(role.id, list(permission_ids.values()))

        self._audit.record(
            action="role.created",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="role",
            entity_id=role.id,
            after={
                "key": role.key,
                "name": role.name,
                "scope": role.scope,
                "portal": role.portal,
                "permission_keys": sorted(permission_ids),
            },
        )
        return role

    def set_role_permissions(
        self,
        *,
        role_id: int,
        permission_keys: list[str],
        actor_user_id: int | None,
    ) -> Role:
        """Replace a role's entire permission set. Audits ``permission.set_changed``
        with the before/after key sets (docs/PF §12)."""
        role = self._repo.get_role(role_id)
        if role is None:
            raise NotFoundError(
                "Role not found.", details={"role_id": role_id}
            )

        before_keys = self._repo.role_permission_keys(role_id)
        permission_ids = self._resolve_permission_ids(permission_keys)

        self._repo.clear_role_permissions(role_id)
        if permission_ids:
            self._repo.add_role_permissions(role_id, list(permission_ids.values()))

        after_keys = set(permission_ids)
        self._audit.record(
            action="permission.set_changed",
            actor_user_id=actor_user_id,
            society_id=role.society_id,
            entity_type="role",
            entity_id=role.id,
            before={"permission_keys": sorted(before_keys)},
            after={"permission_keys": sorted(after_keys)},
        )
        return role

    # --- helpers ---------------------------------------------------------

    def _resolve_permission_ids(self, permission_keys: list[str]) -> dict[str, int]:
        """Map requested permission keys → ids, rejecting any unknown key.

        Dedupes while preserving the caller's intent; a single unknown key fails
        the whole request (all-or-nothing — the client sent a bad catalog key).
        """
        keys = list(dict.fromkeys(permission_keys))  # dedupe, keep order
        if not keys:
            return {}
        found = self._repo.permission_ids_for_keys(keys)
        missing = [k for k in keys if k not in found]
        if missing:
            raise ValidationError(
                "Unknown permission key(s).",
                details={"unknown_permission_keys": missing},
            )
        return {k: found[k] for k in keys}

    def role_permission_keys(self, role_id: int) -> set[str]:
        """Permission keys attached to a role (for response shaping)."""
        return self._repo.role_permission_keys(role_id)

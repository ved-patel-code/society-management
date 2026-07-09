"""DB access for roles/permissions (docs/PF §5, docs/03 §2/§4).

All queries live here — the service owns logic, this layer owns SQL. Every
tenant-scoped query filters by ``society_id``; columns are selected narrowly to
avoid over-fetching (docs/03 §4). No business decisions are made here.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.models import (
    Permission,
    Role,
    RoleModuleVisibility,
    RolePermission,
    SocietyModule,
    UserRole,
)


class RoleRepository:
    """Thin query layer over the role/permission tables."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- roles -----------------------------------------------------------

    def get_role(self, role_id: int) -> Role | None:
        return self._session.get(Role, role_id)

    def global_templates_by_keys(self, keys: tuple[str, ...]) -> list[Role]:
        """Global (``society_id IS NULL``) role rows for the given keys."""
        if not keys:
            return []
        return list(
            self._session.execute(
                select(Role).where(
                    Role.society_id.is_(None), Role.key.in_(keys)
                )
            ).scalars()
        )

    def society_role_keys(self, society_id: int) -> set[str]:
        """Keys of roles already instantiated for a society (idempotency check)."""
        rows = self._session.execute(
            select(Role.key).where(Role.society_id == society_id)
        ).all()
        return {r[0] for r in rows}

    def society_role_by_key(self, society_id: int, key: str) -> Role | None:
        return self._session.execute(
            select(Role).where(
                Role.society_id == society_id, Role.key == key
            )
        ).scalar_one_or_none()

    def add_role(self, role: Role) -> Role:
        self._session.add(role)
        self._session.flush()  # assign PK within the txn (no commit)
        return role

    # --- permissions -----------------------------------------------------

    def permission_ids_for_keys(self, keys: list[str]) -> dict[str, int]:
        """Map each existing permission key to its id (missing keys are absent)."""
        if not keys:
            return {}
        rows = self._session.execute(
            select(Permission.key, Permission.id).where(Permission.key.in_(keys))
        ).all()
        return {key: pid for key, pid in rows}

    def role_permission_ids(self, role_id: int) -> list[int]:
        rows = self._session.execute(
            select(RolePermission.permission_id).where(
                RolePermission.role_id == role_id
            )
        ).all()
        return [r[0] for r in rows]

    def role_permission_keys(self, role_id: int) -> set[str]:
        """Permission keys currently attached to a single role."""
        rows = self._session.execute(
            select(Permission.key)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
        ).all()
        return {r[0] for r in rows}

    def add_role_permissions(self, role_id: int, permission_ids: list[int]) -> None:
        for pid in permission_ids:
            self._session.add(
                RolePermission(role_id=role_id, permission_id=pid)
            )
        self._session.flush()

    def clear_role_permissions(self, role_id: int) -> None:
        for rp in self._session.execute(
            select(RolePermission).where(RolePermission.role_id == role_id)
        ).scalars():
            self._session.delete(rp)
        self._session.flush()

    # --- role_module_visibility ------------------------------------------

    def copy_role_module_visibility(
        self, source_role_id: int, target_role_id: int
    ) -> None:
        """Copy a template role's visibility rows onto a new society role."""
        rows = self._session.execute(
            select(
                RoleModuleVisibility.module_key, RoleModuleVisibility.visible
            ).where(RoleModuleVisibility.role_id == source_role_id)
        ).all()
        for module_key, visible in rows:
            self._session.add(
                RoleModuleVisibility(
                    role_id=target_role_id,
                    module_key=module_key,
                    visible=visible,
                )
            )
        if rows:
            self._session.flush()

    # --- user-scoped reads (effective permissions / portals / modules) ---

    def effective_permission_keys(
        self, user_id: int, society_id: int
    ) -> set[str]:
        """Union of permission keys across the user's roles in the society."""
        rows = self._session.execute(
            select(Permission.key)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(UserRole, UserRole.role_id == RolePermission.role_id)
            .where(
                UserRole.user_id == user_id,
                UserRole.society_id == society_id,
            )
        ).all()
        return {r[0] for r in rows}

    def user_ids_with_permission(
        self, society_id: int, permission_key: str
    ) -> set[int]:
        """The set of user ids holding ``permission_key`` in a society (docs/05 §3).

        The reverse of :meth:`effective_permission_keys`: resolve "who are the
        admins" data-driven — a recipient of a ``complaint_new`` alert is whoever
        currently holds ``complaints.read_all``, via ANY of their roles in the
        society (no frozen recipient list). ``DISTINCT`` because a user may hold
        the permission through multiple roles. Joins
        ``permissions → role_permissions → user_roles`` filtered to the society.
        """
        rows = self._session.execute(
            select(UserRole.user_id)
            .join(RolePermission, RolePermission.role_id == UserRole.role_id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(
                UserRole.society_id == society_id,
                Permission.key == permission_key,
            )
            .distinct()
        ).all()
        return {r[0] for r in rows}

    def user_portals(self, user_id: int, society_id: int) -> list[str]:
        """Distinct ``roles.portal`` across the user's roles in the society."""
        rows = self._session.execute(
            select(Role.portal)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(
                UserRole.user_id == user_id,
                UserRole.society_id == society_id,
            )
            .distinct()
        ).all()
        return [r[0] for r in rows]

    def visible_module_keys_for_portal(
        self, user_id: int, society_id: int, portal: str
    ) -> list[str]:
        """Module keys the user's roles (matching ``portal``) mark visible AND the
        society has enabled — the intersection, computed in one query (docs/03 §4).
        """
        rows = self._session.execute(
            select(SocietyModule.module_key)
            .join(
                RoleModuleVisibility,
                RoleModuleVisibility.module_key == SocietyModule.module_key,
            )
            .join(Role, Role.id == RoleModuleVisibility.role_id)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(
                UserRole.user_id == user_id,
                UserRole.society_id == society_id,
                Role.portal == portal,
                RoleModuleVisibility.visible.is_(True),
                SocietyModule.society_id == society_id,
                SocietyModule.enabled.is_(True),
            )
            .distinct()
        ).all()
        return [r[0] for r in rows]

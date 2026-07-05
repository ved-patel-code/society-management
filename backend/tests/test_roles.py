"""Focused, DB-free unit tests for RoleService logic (P2).

Mirrors the smoke-suite philosophy (no DB): a tiny in-memory fake repository
exercises the branch logic — idempotent template copy, permission-key validation,
and portal/module pass-through. The full DB-backed suite lands with the later test
gate.
"""
from __future__ import annotations

import pytest

from app.common.errors import ConflictError, NotFoundError, ValidationError
from app.platform.bootstrap import SOCIETY_DEFAULT_ROLE_KEYS, SOCIETY_ADMIN, RESIDENT
from app.platform.models import Role
from app.platform.roles.service import RoleService


class _FakeRepo:
    """Minimal in-memory stand-in for RoleRepository."""

    def __init__(self) -> None:
        self._next_id = 1
        self.roles: dict[int, Role] = {}
        self.perm_catalog = {"houses.view": 10, "houses.edit": 11}
        self.role_perms: dict[int, list[int]] = {}
        # Seed the two global templates (society_id=None).
        for tmpl in (SOCIETY_ADMIN, RESIDENT):
            self._add(Role(society_id=None, key=tmpl.key, name=tmpl.name,
                           is_system=True, scope=tmpl.scope, portal=tmpl.portal))

    def _add(self, role: Role) -> Role:
        role.id = self._next_id
        self._next_id += 1
        self.roles[role.id] = role
        return role

    # queries used by the service
    def society_role_keys(self, society_id):
        return {r.key for r in self.roles.values() if r.society_id == society_id}

    def global_templates_by_keys(self, keys):
        return [r for r in self.roles.values()
                if r.society_id is None and r.key in keys]

    def society_role_by_key(self, society_id, key):
        return next((r for r in self.roles.values()
                     if r.society_id == society_id and r.key == key), None)

    def get_role(self, role_id):
        return self.roles.get(role_id)

    def add_role(self, role):
        return self._add(role)

    def role_permission_ids(self, role_id):
        return list(self.role_perms.get(role_id, []))

    def role_permission_keys(self, role_id):
        by_id = {v: k for k, v in self.perm_catalog.items()}
        return {by_id[pid] for pid in self.role_perms.get(role_id, [])}

    def add_role_permissions(self, role_id, permission_ids):
        self.role_perms.setdefault(role_id, []).extend(permission_ids)

    def clear_role_permissions(self, role_id):
        self.role_perms[role_id] = []

    def copy_role_module_visibility(self, source_role_id, target_role_id):
        pass

    def permission_ids_for_keys(self, keys):
        return {k: self.perm_catalog[k] for k in keys if k in self.perm_catalog}


class _NoAudit:
    def record(self, **kwargs):  # noqa: D401 - test stub
        return None


def _service():
    svc = RoleService.__new__(RoleService)
    svc._session = None
    svc._repo = _FakeRepo()
    svc._audit = _NoAudit()
    return svc


def test_instantiate_is_idempotent() -> None:
    svc = _service()
    first = svc.instantiate_society_roles(1, actor_user_id=None)
    assert set(first) == set(SOCIETY_DEFAULT_ROLE_KEYS)
    # Second run creates nothing new but returns the same ids.
    before = len(svc._repo.roles)
    second = svc.instantiate_society_roles(1, actor_user_id=None)
    assert second == first
    assert len(svc._repo.roles) == before


def test_create_role_rejects_duplicate_key() -> None:
    svc = _service()
    svc.create_role(society_id=1, key="tenant", name="Tenant", portal="resident",
                    scope="society", permission_keys=[], actor_user_id=None)
    with pytest.raises(ConflictError):
        svc.create_role(society_id=1, key="tenant", name="Tenant 2",
                        portal="resident", scope="society",
                        permission_keys=[], actor_user_id=None)


def test_create_role_rejects_unknown_permission() -> None:
    svc = _service()
    with pytest.raises(ValidationError):
        svc.create_role(society_id=1, key="tenant", name="Tenant", portal="resident",
                        scope="society", permission_keys=["houses.nope"],
                        actor_user_id=None)


def test_set_permissions_replaces_set() -> None:
    svc = _service()
    role = svc.create_role(society_id=1, key="tenant", name="Tenant",
                           portal="resident", scope="society",
                           permission_keys=["houses.view"], actor_user_id=None)
    svc.set_role_permissions(role_id=role.id,
                             permission_keys=["houses.edit"], actor_user_id=None)
    assert svc.role_permission_keys(role.id) == {"houses.edit"}


def test_set_permissions_missing_role() -> None:
    svc = _service()
    with pytest.raises(NotFoundError):
        svc.set_role_permissions(role_id=999, permission_keys=[], actor_user_id=None)

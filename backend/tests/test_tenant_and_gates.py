"""P7 — tenant-scoping + authorization-gate proof tests (real DB).

Proves, against a live Postgres via ``SessionLocal`` (the stack is up):

  (a) ``require_permission`` denies a caller lacking the permission (403
      ``permission_denied``) and admits one holding it (via ``TestClient`` +
      real access tokens minted with ``create_access_token``).
  (b) ``require_module`` denies when the society has NOT enabled the module
      (403 ``module_disabled``) and passes when it has — exercised by calling the
      dependency callable directly with a real session (foundation has no
      society-scoped ``/{module}/*`` routes yet).
  (c) cross-tenant isolation — a Society-A context cannot read Society-B rows
      through the repositories (and vice-versa).
  (d) super_admin bypass — ``require_module`` returns the auth context (no 403)
      for a platform actor even with no active society / no module row.

Every row created is torn down in a ``finally`` so runs are deterministic and
isolated; nothing is left behind in the shared DB.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.common.errors import DomainError, ModuleDisabledError
from app.core.db import SessionLocal, get_session
from app.core.deps import (
    AuthContext,
    get_auth_context,
    require_module,
    require_permission,
)
from app.core.security import create_access_token, hash_password
from app.platform.models import (
    AuditLog,
    Permission,
    Role,
    RoleModuleVisibility,
    RolePermission,
    Society,
    SocietyModule,
    User,
    UserRole,
)
from app.platform.roles.service import RoleService
from app.platform.societies.schemas import ModuleAllocation, SocietyCreate
from app.platform.societies.service import SocietyService

# Unique suffix so parallel/repeat runs never collide on emails/keys.
_TAG = uuid.uuid4().hex[:8]

_PERM_KEY = f"p7audit.{_TAG}.do_thing"
_MODULE_KEY = f"p7mod_{_TAG}"


# --------------------------------------------------------------------------- #
# Fixture: build two societies (A, B) with roles-by-copy, a permission, a role
# in A that holds it, and a user assigned that role in A. Yields a bag of ids;
# tears everything down afterwards.
# --------------------------------------------------------------------------- #


class _Fixture:
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


@pytest.fixture()
def world():  # noqa: C901 - linear setup/teardown, easier read top-to-bottom
    session = SessionLocal()
    created_user_ids: list[int] = []
    created_society_ids: list[int] = []
    perm_id: int | None = None
    try:
        svc = SocietyService(session)
        society_a = svc.create_society(
            SocietyCreate(
                name=f"P7 Society A {_TAG}",
                storage_limit_bytes=1_000_000,
                default_member_password="DefaultPass123",
            ),
            actor_user_id=None,
        )
        society_b = svc.create_society(
            SocietyCreate(
                name=f"P7 Society B {_TAG}",
                storage_limit_bytes=1_000_000,
                default_member_password="DefaultPass123",
            ),
            actor_user_id=None,
        )
        created_society_ids.extend([society_a.id, society_b.id])

        # A test permission in the (global) catalog.
        perm = Permission(
            key=_PERM_KEY, module_key="p7audit", description="P7 test perm"
        )
        session.add(perm)
        session.flush()
        perm_id = perm.id

        # A society-scoped role in A that HOLDS the permission, and one that lacks
        # it. Uses RoleService so the create path is real.
        roles = RoleService(session)
        role_with = roles.create_role(
            society_id=society_a.id,
            key=f"p7_with_{_TAG}",
            name="P7 With Perm",
            portal="admin",
            scope="society",
            permission_keys=[_PERM_KEY],
            actor_user_id=None,
        )
        role_without = roles.create_role(
            society_id=society_a.id,
            key=f"p7_without_{_TAG}",
            name="P7 Without Perm",
            portal="admin",
            scope="society",
            permission_keys=[],
            actor_user_id=None,
        )

        # Two users: one granted the role holding the perm, one granted the role
        # lacking it — both scoped to society A.
        user_with = User(
            email=f"p7-with-{_TAG}@example.com",
            password_hash=hash_password("DefaultPass123"),
            password_state="active",
            is_active=True,
        )
        user_without = User(
            email=f"p7-without-{_TAG}@example.com",
            password_hash=hash_password("DefaultPass123"),
            password_state="active",
            is_active=True,
        )
        session.add_all([user_with, user_without])
        session.flush()
        created_user_ids.extend([user_with.id, user_without.id])

        session.add_all(
            [
                UserRole(
                    user_id=user_with.id,
                    society_id=society_a.id,
                    role_id=role_with.id,
                ),
                UserRole(
                    user_id=user_without.id,
                    society_id=society_a.id,
                    role_id=role_without.id,
                ),
            ]
        )
        session.commit()

        yield _Fixture(
            society_a_id=society_a.id,
            society_b_id=society_b.id,
            role_with_id=role_with.id,
            role_without_id=role_without.id,
            user_with_id=user_with.id,
            user_without_id=user_without.id,
        )
    finally:
        # Teardown in FK-safe order, only the rows we created. create_society and
        # RoleService also emit audit_log rows and copy the default (society_admin/
        # resident) roles + their role_permissions/role_module_visibility — all of
        # which must be removed before the parent societies.
        session.rollback()
        for sid in created_society_ids:
            session.execute(delete(UserRole).where(UserRole.society_id == sid))
            role_ids = [
                r
                for (r,) in session.execute(
                    select(Role.id).where(Role.society_id == sid)
                ).all()
            ]
            if role_ids:
                session.execute(
                    delete(RolePermission).where(
                        RolePermission.role_id.in_(role_ids)
                    )
                )
                session.execute(
                    delete(RoleModuleVisibility).where(
                        RoleModuleVisibility.role_id.in_(role_ids)
                    )
                )
                session.execute(delete(Role).where(Role.id.in_(role_ids)))
            session.execute(
                delete(SocietyModule).where(SocietyModule.society_id == sid)
            )
            session.execute(delete(AuditLog).where(AuditLog.society_id == sid))
        for uid in created_user_ids:
            session.execute(delete(UserRole).where(UserRole.user_id == uid))
            session.execute(delete(AuditLog).where(AuditLog.actor_user_id == uid))
        if perm_id is not None:
            session.execute(
                delete(RolePermission).where(RolePermission.permission_id == perm_id)
            )
            session.execute(delete(Permission).where(Permission.id == perm_id))
        for uid in created_user_ids:
            session.execute(delete(User).where(User.id == uid))
        for sid in created_society_ids:
            session.execute(delete(Society).where(Society.id == sid))
        session.commit()
        session.close()


# --------------------------------------------------------------------------- #
# (a) require_permission — HTTP, real tokens.
# --------------------------------------------------------------------------- #


def _app_with_perm_route() -> FastAPI:
    """A throwaway app with ONE route gated by require_permission(_PERM_KEY).

    Not mounted on the production app — proves the gate without touching prod
    routers.
    """
    app = FastAPI()

    # Same DomainError -> {code,message,details} mapping the production app uses,
    # so typed errors render as their real HTTP status (403) instead of a 500.
    @app.exception_handler(DomainError)
    async def _domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.get("/p7/guarded-perm")
    def _guarded(auth: AuthContext = Depends(require_permission(_PERM_KEY))):
        return {"ok": True, "user_id": auth.user_id}

    return app


def test_require_permission_denies_and_allows(world) -> None:
    app = _app_with_perm_route()
    client = TestClient(app, raise_server_exceptions=False)

    token_with = create_access_token(
        user_id=world.user_with_id,
        active_society_id=world.society_a_id,
        role_ids=[world.role_with_id],
        password_state="active",
    )
    token_without = create_access_token(
        user_id=world.user_without_id,
        active_society_id=world.society_a_id,
        role_ids=[world.role_without_id],
        password_state="active",
    )

    # Holder passes.
    resp_ok = client.get(
        "/p7/guarded-perm",
        headers={"Authorization": f"Bearer {token_with}"},
    )
    assert resp_ok.status_code == 200, resp_ok.text
    assert resp_ok.json()["user_id"] == world.user_with_id

    # Non-holder is denied (403 permission_denied, naming the required perm).
    resp_denied = client.get(
        "/p7/guarded-perm",
        headers={"Authorization": f"Bearer {token_without}"},
    )
    assert resp_denied.status_code == 403, resp_denied.text
    body = resp_denied.json()
    assert body["code"] == "permission_denied"
    assert body["details"]["required_permission"] == _PERM_KEY

    # No credentials → 401.
    resp_anon = client.get("/p7/guarded-perm")
    assert resp_anon.status_code == 401


# --------------------------------------------------------------------------- #
# (b) require_module — dependency callable, real session.
# --------------------------------------------------------------------------- #


def _auth_for(society_id: int, user_id: int) -> AuthContext:
    return AuthContext(
        user=None,  # not read by require_module
        user_id=user_id,
        active_society_id=society_id,
        role_ids=[],
        password_state="active",
        is_super_admin=False,
        permission_keys=set(),
    )


def test_require_module_denies_when_disabled_and_passes_when_enabled(world) -> None:
    dep = require_module(_MODULE_KEY)
    session = SessionLocal()
    try:
        auth = _auth_for(world.society_a_id, world.user_with_id)

        # No SocietyModule row yet → module disabled → 403.
        with pytest.raises(ModuleDisabledError) as exc:
            dep(auth=auth, session=session)
        assert exc.value.status_code == 403
        assert exc.value.code == "module_disabled"
        assert exc.value.details["module_key"] == _MODULE_KEY

        # Enable the module for society A, then it passes.
        session.add(
            SocietyModule(
                society_id=world.society_a_id,
                module_key=_MODULE_KEY,
                enabled=True,
            )
        )
        session.commit()

        returned = dep(auth=auth, session=session)
        assert returned is auth  # gate returns the auth context unchanged

        # A disabled row (enabled=False) must still be denied.
        session.execute(
            delete(SocietyModule).where(
                SocietyModule.society_id == world.society_a_id,
                SocietyModule.module_key == _MODULE_KEY,
            )
        )
        session.add(
            SocietyModule(
                society_id=world.society_a_id,
                module_key=_MODULE_KEY,
                enabled=False,
            )
        )
        session.commit()
        with pytest.raises(ModuleDisabledError):
            dep(auth=auth, session=session)
    finally:
        session.execute(
            delete(SocietyModule).where(
                SocietyModule.society_id == world.society_a_id,
                SocietyModule.module_key == _MODULE_KEY,
            )
        )
        session.commit()
        session.close()


def test_require_module_denies_when_no_active_society(world) -> None:
    dep = require_module(_MODULE_KEY)
    session = SessionLocal()
    try:
        auth = _auth_for(None, world.user_with_id)  # type: ignore[arg-type]
        with pytest.raises(ModuleDisabledError):
            dep(auth=auth, session=session)
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# (c) cross-tenant isolation — repositories scoped to B never see A's rows.
# --------------------------------------------------------------------------- #


def test_cross_tenant_isolation(world) -> None:
    from app.platform.roles.repository import RoleRepository
    from app.platform.societies.repository import SocietyRepository
    from app.platform.users.repository import UserRepository

    session = SessionLocal()
    try:
        roles = RoleRepository(session)
        users = UserRepository(session)
        socs = SocietyRepository(session)

        # The custom role lives in A; a lookup scoped to B must not find it.
        assert (
            roles.society_role_by_key(world.society_a_id, f"p7_with_{_TAG}")
            is not None
        )
        assert (
            roles.society_role_by_key(world.society_b_id, f"p7_with_{_TAG}")
            is None
        )
        assert f"p7_with_{_TAG}" in roles.society_role_keys(world.society_a_id)
        assert f"p7_with_{_TAG}" not in roles.society_role_keys(world.society_b_id)

        # Effective-permission union: the user holds the perm in A, and querying
        # the SAME user scoped to B yields nothing (isolation of the authZ union).
        assert _PERM_KEY in roles.effective_permission_keys(
            world.user_with_id, world.society_a_id
        )
        assert (
            roles.effective_permission_keys(world.user_with_id, world.society_b_id)
            == set()
        )

        # Portals: the user has an 'admin' portal in A, none in B.
        assert "admin" in roles.user_portals(world.user_with_id, world.society_a_id)
        assert roles.user_portals(world.user_with_id, world.society_b_id) == []

        # user_roles reachable only under the owning society.
        assert (
            users.get_user_role(
                world.user_with_id, world.society_a_id, world.role_with_id
            )
            is not None
        )
        assert (
            users.get_user_role(
                world.user_with_id, world.society_b_id, world.role_with_id
            )
            is None
        )

        # Society B exists and is a distinct row from A.
        assert socs.get(world.society_b_id) is not None
        assert world.society_a_id != world.society_b_id
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# (d) super_admin bypass — require_module returns auth, no 403, no module row.
# --------------------------------------------------------------------------- #


def test_super_admin_bypasses_require_module() -> None:
    dep = require_module(f"anything_{_TAG}")
    session = SessionLocal()
    try:
        auth = AuthContext(
            user=None,
            user_id=0,
            active_society_id=None,  # platform actor: no active society
            role_ids=[],
            password_state="active",
            is_super_admin=True,
            permission_keys=set(),
        )
        # No SocietyModule row anywhere for this key, yet the platform actor passes.
        returned = dep(auth=auth, session=session)
        assert returned is auth
    finally:
        session.close()


def test_super_admin_has_permission_without_perm_rows() -> None:
    """require_permission short-circuits True for a super_admin (flag-based)."""
    auth = AuthContext(
        user=None,
        user_id=0,
        active_society_id=None,
        role_ids=[],
        password_state="active",
        is_super_admin=True,
        permission_keys=set(),
    )
    dep = require_permission(f"any.perm.{_TAG}")
    assert dep(auth=auth) is auth

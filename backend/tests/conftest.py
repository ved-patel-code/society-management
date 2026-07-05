"""Shared test harness for the Platform Foundation suite.

Isolation strategy: **truncate-and-reseed per test**. Before each test every
table is truncated and the platform baseline (permissions + global role templates
+ one super-admin) is re-seeded. This is bulletproof with the app's real
transaction handling — including the auth theft path, which commits on its own
independent ``SessionLocal`` — where SAVEPOINT-based rollback isolation would be
fragile. The schema is tiny, so truncate+seed is fast.

Fixtures provided:
- ``db``            — a raw SQLAlchemy Session on the app engine (for arrange/assert).
- ``client``        — FastAPI ``TestClient`` (uses the app's real ``get_session``).
- ``superadmin``    — the seeded platform super-admin ``User``.
- ``society``       — a freshly created society (roles copied) via ``SocietyService``.
- ``admin_user``    — a provisioned ``society_admin`` for ``society`` (must_change).
- ``resident_user`` — a provisioned ``resident`` for ``society`` (must_change).
- ``auth`` helpers  — ``login(email, password)`` + ``bearer(token)`` for HTTP tests.
- ``make_token``    — mint an access token directly (crafted-claims security tests).
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

# IMPORTANT: this import MUST come before any ``app.*`` import. It points
# DATABASE_URL at this xdist worker's own test DB (and creates + migrates it),
# so the app binds to the correct per-worker database when first imported below.
import tests._worker_db  # noqa: F401  (import for its import-time side effect)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.security import create_access_token
from app.main import app
from app.platform.models import Society, User
from app.platform.societies.schemas import SocietyCreate
from app.platform.societies.service import SocietyService
from app.platform.users.provisioning import UserProvisioningService
from tests.conftest_dbguard import assert_test_database

# Fail fast if this destructive suite is pointed at a non-test database.
assert_test_database()

# Baseline credentials seeded for every test (independent of env).
SUPERADMIN_EMAIL = "root@platform.test"
SUPERADMIN_PASSWORD = "RootPass123"
DEFAULT_MEMBER_PASSWORD = "Welcome123"


def _all_table_names() -> list[str]:
    """Every mapped table, derived from SQLAlchemy metadata.

    Deriving this dynamically (rather than a hardcoded list) means a future module
    that adds tables is truncated automatically once its models import — no edit to
    this harness is ever required. ``import app.platform.models`` (via ``app.main``)
    registers the foundation tables; future modules register theirs the same way.
    """
    from app.core.db import Base

    return [t.name for t in Base.metadata.sorted_tables]


def _truncate_all(db: Session) -> None:
    tables = _all_table_names()
    if not tables:
        return
    db.execute(
        text("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY CASCADE")
    )
    db.commit()


def _seed_baseline(db: Session) -> None:
    """Seed permission catalog + global role templates + one super-admin.

    Mirrors ``app.cli.seed`` but is self-contained and env-independent so tests
    are deterministic.
    """
    from app.core.registry import MODULE_REGISTRY
    from app.core.security import hash_password
    from app.platform.bootstrap import GLOBAL_ROLE_TEMPLATES, register_foundation
    from app.platform.models import Permission, Role

    register_foundation()

    # Permissions from the registry (foundation has none today; future-proof).
    existing_perms = {k for (k,) in db.execute(text("SELECT key FROM permissions"))}
    for perm in MODULE_REGISTRY.all_permission_keys():
        if perm.key not in existing_perms:
            module_key = perm.key.split(".", 1)[0] if "." in perm.key else perm.key
            db.add(
                Permission(
                    key=perm.key, module_key=module_key, description=perm.description
                )
            )

    for tmpl in GLOBAL_ROLE_TEMPLATES:
        db.add(
            Role(
                society_id=None,
                key=tmpl.key,
                name=tmpl.name,
                is_system=True,
                scope=tmpl.scope,
                portal=tmpl.portal,
            )
        )

    db.add(
        User(
            email=SUPERADMIN_EMAIL,
            password_hash=hash_password(SUPERADMIN_PASSWORD),
            password_state="active",
            is_platform_super_admin=True,
            full_name="Platform Root",
            is_active=True,
        )
    )
    db.commit()


@pytest.fixture(autouse=True)
def _reset_db() -> Iterator[None]:
    """Truncate + reseed before every test (autouse → total isolation)."""
    db = SessionLocal()
    try:
        _truncate_all(db)
        _seed_baseline(db)
    finally:
        db.close()
    yield


@pytest.fixture
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def superadmin(db: Session) -> User:
    return db.query(User).filter(User.email == SUPERADMIN_EMAIL).one()


@pytest.fixture
def society(db: Session, superadmin: User) -> Society:
    """A fresh society (status onboarding, roles copied) created by the super-admin."""
    soc = SocietyService(db).create_society(
        SocietyCreate(
            name="Test Society",
            storage_limit_bytes=5 * 1024**3,
            default_member_password=DEFAULT_MEMBER_PASSWORD,
        ),
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(soc)
    return soc


@pytest.fixture
def admin_user(db: Session, society: Society, superadmin: User) -> User:
    user = UserProvisioningService(db).create_or_link_user(
        email="admin@test.local",
        society_id=society.id,
        role_key="society_admin",
        profile={"full_name": "Society Admin"},
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def resident_user(db: Session, society: Society, superadmin: User) -> User:
    user = UserProvisioningService(db).create_or_link_user(
        email="resident@test.local",
        society_id=society.id,
        role_key="resident",
        profile={"full_name": "Resident One"},
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(user)
    return user


@dataclass
class AuthHelper:
    client: TestClient

    def login(self, email: str, password: str):
        return self.client.post(
            "/auth/login", json={"email": email, "password": password}
        )

    def bearer(self, access_token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}"}

    def login_ok(self, email: str, password: str) -> dict:
        resp = self.login(email, password)
        assert resp.status_code == 200, resp.text
        return resp.json()


@pytest.fixture
def auth(client: TestClient) -> AuthHelper:
    return AuthHelper(client)


@pytest.fixture
def make_token():
    """Mint an access token with explicit claims (crafted-claims security tests)."""

    def _make(
        *,
        user_id: int,
        active_society_id: int | None = None,
        role_ids: list[int] | None = None,
        password_state: str = "active",
    ) -> str:
        return create_access_token(
            user_id=user_id,
            active_society_id=active_society_id,
            role_ids=role_ids or [],
            password_state=password_state,
        )

    return _make


# Re-export constants for tests that need the known credentials.
__all__ = [
    "SUPERADMIN_EMAIL",
    "SUPERADMIN_PASSWORD",
    "DEFAULT_MEMBER_PASSWORD",
    "AuthHelper",
]

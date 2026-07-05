"""Idempotent platform seed (docs/PF §2 step 1, §14.4).

Run:  python -m app.cli.seed   (inside the backend container)

Creates, without duplicating on re-run:
  1. the permission catalog from ``MODULE_REGISTRY``,
  2. the global role templates (super_admin / society_admin / resident),
  3. the first super_admin user from env vars, and grants it super_admin.

There is no public signup for the super-admin — this CLI is the only path.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.registry import MODULE_REGISTRY
from app.core.security import hash_password
from app.platform.bootstrap import (
    GLOBAL_ROLE_TEMPLATES,
    SUPER_ADMIN,
    register_foundation,
)
from app.platform.models import (
    Permission,
    Role,
    User,
    UserRole,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.cli.seed")


def seed_permissions(session: Session) -> int:
    """Upsert every registered module's permissions by unique ``key``."""
    existing = {
        k for (k,) in session.execute(select(Permission.key)).all()
    }
    created = 0
    for perm in MODULE_REGISTRY.all_permission_keys():
        if perm.key not in existing:
            session.add(
                Permission(
                    key=perm.key,
                    module_key=_module_key_for(perm.key),
                    description=perm.description,
                )
            )
            created += 1
    return created


def _module_key_for(perm_key: str) -> str:
    """Derive the owning module key from a permission key ("houses.x" -> "houses")."""
    return perm_key.split(".", 1)[0] if "." in perm_key else perm_key


def seed_role_templates(session: Session) -> int:
    """Create the global (society_id NULL) role templates if absent."""
    created = 0
    for tmpl in GLOBAL_ROLE_TEMPLATES:
        exists = session.execute(
            select(Role.id).where(Role.society_id.is_(None), Role.key == tmpl.key)
        ).scalar_one_or_none()
        if exists is None:
            session.add(
                Role(
                    society_id=None,
                    key=tmpl.key,
                    name=tmpl.name,
                    is_system=True,
                    scope=tmpl.scope,
                    portal=tmpl.portal,
                )
            )
            created += 1
    return created


def seed_super_admin(session: Session) -> bool:
    """Create the first super_admin from env if it doesn't already exist."""
    email = settings.superadmin_email.strip().lower()
    if not email or not settings.superadmin_password:
        logger.warning(
            "SUPERADMIN_EMAIL / SUPERADMIN_PASSWORD not set — skipping super_admin seed."
        )
        return False

    user = session.execute(
        select(User).where(User.email == email)
    ).scalar_one_or_none()
    if user is None:
        user = User(
            email=email,
            password_hash=hash_password(settings.superadmin_password),
            password_state="active",  # bootstrap admin is ready to use immediately
            is_platform_super_admin=True,
            full_name=settings.superadmin_full_name,
            is_active=True,
        )
        session.add(user)
        session.flush()
        logger.info("Created super_admin user %s (id=%s).", email, user.id)
    else:
        logger.info("Super_admin user %s already exists (id=%s).", email, user.id)

    # Super-admin authority comes from the ``is_platform_super_admin`` flag, which
    # alone drives /admin/* access (see core/deps.py). We deliberately do NOT create
    # a society-scoped ``user_roles`` row for it — the platform actor is above any
    # single society (docs/PF §7).
    return True


def main() -> None:
    register_foundation()
    session = SessionLocal()
    try:
        perms = seed_permissions(session)
        roles = seed_role_templates(session)
        seed_super_admin(session)
        session.commit()
        logger.info(
            "Seed complete. permissions +%d, role_templates +%d.", perms, roles
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()

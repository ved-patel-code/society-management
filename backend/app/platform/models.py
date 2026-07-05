"""All 11 Platform Foundation tables (docs/PF §3) — the frozen schema.

Single lead-owned source of the foundation schema. Feature sub-agents READ these
models (import the classes) but add NO new tables. Alembic autogenerate imports
this module (via ``app.platform.models``) so every table is seen.

Rules honored here (docs/03 §3/§5, docs/PF §3):
- BIGINT identity PK + ``created_at``/``updated_at`` come from ``Base``.
- DB holds ONLY integrity constraints (PK/FK/NOT NULL/UNIQUE) — no business rules.
- Every tenant table carries ``society_id``; composite uniques/indexes lead with it.
- ``users.email`` is CITEXT (case-insensitive global login id).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# --- Enum-like string domains (enforced in the service layer, not the DB) ---
# societies.type:   building | individual_houses     (NULL until onboarding step 1)
# societies.status: onboarding | active | suspended
# users.password_state: must_change | active
# roles.scope:  platform | society
# roles.portal: admin | resident | platform


class Society(Base):
    __tablename__ = "societies"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # NULLABLE — the society_admin picks building vs individual_houses in onboarding.
    type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="onboarding"
    )
    storage_limit_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Super-admin MUST set this at creation; stored hashed (Argon2id), never plaintext.
    default_member_password_hash: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(8), nullable=False, server_default="INR")
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="Asia/Kolkata"
    )
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    # NOTE: no UNIQUE on name — duplicate society names are allowed (docs/PF §14.3).


class SocietyModule(Base):
    """Per-(society, module) feature flag (docs/PF §3)."""

    __tablename__ = "society_modules"
    __table_args__ = (
        UniqueConstraint("society_id", "module_key", name="uq_society_modules_key"),
        Index("ix_society_modules_society", "society_id"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    module_key: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    enabled_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_email", "email", unique=True),)

    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    password_state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="must_change"
    )
    is_platform_super_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RefreshToken(Base):
    """Rotating, revocable refresh token; only the HASH is stored (docs/PF §4)."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user", "user_id"),
        Index("ix_refresh_tokens_hash", "token_hash"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Rotation chain link: the token this one replaced (theft-detection support).
    replaced_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("refresh_tokens.id"), nullable=True
    )
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PasswordReset(Base):
    __tablename__ = "password_resets"
    __table_args__ = (Index("ix_password_resets_user", "user_id"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    temp_password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Role(Base):
    """Data-driven role. ``society_id`` NULL = global template (docs/PF §5)."""

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("society_id", "key", name="uq_roles_society_key"),
    )

    society_id: Mapped[int | None] = mapped_column(
        ForeignKey("societies.id"), nullable=True
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False)  # platform | society
    portal: Mapped[str] = mapped_column(String(16), nullable=False)  # admin|resident|platform


class Permission(Base):
    """Capability catalog, seeded from the module registry (docs/PF §5)."""

    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint("key", name="uq_permissions_key"),
        Index("ix_permissions_module", "module_key"),
    )

    key: Mapped[str] = mapped_column(String(128), nullable=False)
    module_key: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permissions"),
    )

    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)
    permission_id: Mapped[int] = mapped_column(
        ForeignKey("permissions.id"), nullable=False
    )


class UserRole(Base):
    """user ↔ society ↔ role. Many-to-many enables dual-role accounts (docs/PF §5.1)."""

    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "society_id", "role_id", name="uq_user_roles"
        ),
        Index("ix_user_roles_user_society", "user_id", "society_id"),
        Index("ix_user_roles_society_role", "society_id", "role_id"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)
    assigned_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RoleModuleVisibility(Base):
    """Which tabs/modules a role's portal shows (docs/PF §5). View-only hint."""

    __tablename__ = "role_module_visibility"
    __table_args__ = (
        UniqueConstraint("role_id", "module_key", name="uq_role_module_visibility"),
    )

    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)
    module_key: Mapped[str] = mapped_column(String(64), nullable=False)
    visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )


class AuditLog(Base):
    """Append-only record of every state-changing admin action (docs/PF §12)."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_society_at", "society_id", "at"),
        Index("ix_audit_log_actor_at", "actor_user_id", "at"),
    )

    society_id: Mapped[int | None] = mapped_column(
        ForeignKey("societies.id"), nullable=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

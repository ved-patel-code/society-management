"""Platform Foundation initial schema — all 11 tables + CITEXT (docs/PF §3).

Revision ID: 0001_foundation
Revises:
Create Date: 2026-07-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import CITEXT, JSONB

revision: str = "0001_foundation"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    # Case-insensitive email login id (users.email).
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    # --- societies (no FK deps) ---
    op.create_table(
        "societies",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="onboarding"),
        sa.Column("storage_limit_bytes", sa.BigInteger(), nullable=False),
        sa.Column("default_member_password_hash", sa.String(length=255), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="INR"),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column("settings", JSONB(), nullable=False, server_default="{}"),
        *_ts_columns(),
    )

    # --- users (references itself/none) ---
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("email", CITEXT(), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("password_state", sa.String(length=16), nullable=False, server_default="must_change"),
        sa.Column("is_platform_super_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_columns(),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # --- society_modules ---
    op.create_table(
        "society_modules",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("society_id", sa.BigInteger(), sa.ForeignKey("societies.id"), nullable=False),
        sa.Column("module_key", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("config", JSONB(), nullable=False, server_default="{}"),
        sa.Column("enabled_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_columns(),
        sa.UniqueConstraint("society_id", "module_key", name="uq_society_modules_key"),
    )
    op.create_index("ix_society_modules_society", "society_modules", ["society_id"])

    # --- refresh_tokens (self-ref for rotation chain) ---
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", sa.BigInteger(), sa.ForeignKey("refresh_tokens.id"), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        *_ts_columns(),
    )
    op.create_index("ix_refresh_tokens_user", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_hash", "refresh_tokens", ["token_hash"])

    # --- password_resets ---
    op.create_table(
        "password_resets",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("temp_password_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_columns(),
    )
    op.create_index("ix_password_resets_user", "password_resets", ["user_id"])

    # --- roles (society_id NULL = global template) ---
    op.create_table(
        "roles",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("society_id", sa.BigInteger(), sa.ForeignKey("societies.id"), nullable=True),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("portal", sa.String(length=16), nullable=False),
        *_ts_columns(),
        sa.UniqueConstraint("society_id", "key", name="uq_roles_society_key"),
    )

    # --- permissions ---
    op.create_table(
        "permissions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("module_key", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        *_ts_columns(),
        sa.UniqueConstraint("key", name="uq_permissions_key"),
    )
    op.create_index("ix_permissions_module", "permissions", ["module_key"])

    # --- role_permissions ---
    op.create_table(
        "role_permissions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("role_id", sa.BigInteger(), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("permission_id", sa.BigInteger(), sa.ForeignKey("permissions.id"), nullable=False),
        *_ts_columns(),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_role_permissions"),
    )

    # --- user_roles ---
    op.create_table(
        "user_roles",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("society_id", sa.BigInteger(), sa.ForeignKey("societies.id"), nullable=False),
        sa.Column("role_id", sa.BigInteger(), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("assigned_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        *_ts_columns(),
        sa.UniqueConstraint("user_id", "society_id", "role_id", name="uq_user_roles"),
    )
    op.create_index("ix_user_roles_user_society", "user_roles", ["user_id", "society_id"])
    op.create_index("ix_user_roles_society_role", "user_roles", ["society_id", "role_id"])

    # --- role_module_visibility ---
    op.create_table(
        "role_module_visibility",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("role_id", sa.BigInteger(), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("module_key", sa.String(length=64), nullable=False),
        sa.Column("visible", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_ts_columns(),
        sa.UniqueConstraint("role_id", "module_key", name="uq_role_module_visibility"),
    )

    # --- audit_log (append-only) ---
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("society_id", sa.BigInteger(), sa.ForeignKey("societies.id"), nullable=True),
        sa.Column("actor_user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=True),
        sa.Column("entity_id", sa.BigInteger(), nullable=True),
        sa.Column("before", JSONB(), nullable=True),
        sa.Column("after", JSONB(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        *_ts_columns(),
    )
    op.create_index("ix_audit_log_society_at", "audit_log", ["society_id", "at"])
    op.create_index("ix_audit_log_actor_at", "audit_log", ["actor_user_id", "at"])


def downgrade() -> None:
    for table in (
        "audit_log",
        "role_module_visibility",
        "user_roles",
        "role_permissions",
        "permissions",
        "roles",
        "password_resets",
        "refresh_tokens",
        "society_modules",
        "users",
        "societies",
    ):
        op.drop_table(table)
    op.execute("DROP EXTENSION IF EXISTS citext")

"""notifications module

Revision ID: 0008_notifications
Revises: 0007_notices
Create Date: 2026-07-08 00:00:00.000000+00:00

Adds the single Notifications table (docs/modules/notifications.md §3): one row
per recipient per event. The DB holds ONLY integrity constraints (PK/FK/NOT
NULL/UNIQUE) + the indexes the feed/badge, mark-read, and purge queries need
(docs/03 §5); all engine logic (recipient resolution, dedupe, cadence,
clear-on-read) lives in the service layer.

Indexes (docs §3):
- partial UNIQUE(society_id, dedupe_key) WHERE dedupe_key IS NOT NULL — the
  idempotency backstop for scheduled fires (a worker re-run can't double-post).
- (user_id, read_at) WHERE read_at IS NULL — the hot unread feed + badge count.
- (user_id, entity_type, entity_id) WHERE read_at IS NULL — the mark_read_for
  clear-on-read lookup.
- (read_at) — the daily read-purge scan.

No preferences/deliveries table (in-app only, no opt-out in v1). Reminder rules
live in code, not a table (docs §3/§4). No FK cascade (integrity net only).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0008_notifications'
down_revision: Union[str, None] = '0007_notices'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _base_cols() -> list:
    """The columns every table carries (Base): id PK + created_at/updated_at."""
    return [
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        'notifications',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('type', sa.String(length=32), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('entity_type', sa.String(length=32), nullable=True),
        sa.Column('entity_id', sa.BigInteger(), nullable=True),
        sa.Column('dedupe_key', sa.String(length=128), nullable=True),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        *_base_cols(),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    # Idempotency: at most one row per (society, dedupe_key) when a key is set.
    op.create_index(
        'uq_notifications_society_dedupe', 'notifications',
        ['society_id', 'dedupe_key'], unique=True,
        postgresql_where=sa.text('dedupe_key IS NOT NULL'),
    )
    # Hot path: the caller's unread feed + badge count.
    op.create_index(
        'ix_notifications_user_unread', 'notifications',
        ['user_id', 'created_at'], unique=False,
        postgresql_where=sa.text('read_at IS NULL'),
    )
    # Clear-on-read: a user's pending notifications for one entity.
    op.create_index(
        'ix_notifications_user_entity_unread', 'notifications',
        ['user_id', 'entity_type', 'entity_id'], unique=False,
        postgresql_where=sa.text('read_at IS NULL'),
    )
    # The daily read-purge scan (rows whose read_at is older than retention).
    op.create_index(
        'ix_notifications_read_at', 'notifications',
        ['read_at'], unique=False,
        postgresql_where=sa.text('read_at IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('ix_notifications_read_at', table_name='notifications')
    op.drop_index('ix_notifications_user_entity_unread', table_name='notifications')
    op.drop_index('ix_notifications_user_unread', table_name='notifications')
    op.drop_index('uq_notifications_society_dedupe', table_name='notifications')
    op.drop_table('notifications')

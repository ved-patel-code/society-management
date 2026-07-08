"""notice board module

Revision ID: 0007_notices
Revises: 0006_complaints
Create Date: 2026-07-08 00:00:00.000000+00:00

Adds the three Notice Board tables (docs/modules/notice-board.md §3): notices,
notice_attachments, notice_reads. The DB holds ONLY integrity constraints
(PK/FK/NOT NULL/UNIQUE) + the composite/partial indexes the feed's common query
needs (docs/03 §5); all business rules (transition table, query-time expiry, pin
ordering, read/receipt logic) live in the service layer.

Notices are society-scoped (a whole-society broadcast — no house_id). One partial
index serves the hot active-feed path (published rows, pinned-first, newest-first);
the composite index serves the admin filter view. ``notice_reads`` carries a
UNIQUE(notice_id, user_id) — the backstop for the idempotent read insert. Tables
are created in FK-dependency order; downgrade drops in reverse (indexes first). No
FK cascade (append-only convention).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0007_notices'
down_revision: Union[str, None] = '0006_complaints'
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
    # --- notices -----------------------------------------------------------
    op.create_table(
        'notices',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), server_default='draft', nullable=False),
        sa.Column('is_pinned', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_edited_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.BigInteger(), nullable=False),
        sa.Column('withdrawn_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('withdrawn_by', sa.BigInteger(), nullable=True),
        *_base_cols(),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['withdrawn_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_notices_society_status_pinned_published', 'notices',
        ['society_id', 'status', 'is_pinned', 'published_at'], unique=False,
    )
    op.create_index(
        'ix_notices_active_feed', 'notices',
        ['society_id', 'is_pinned', 'published_at'], unique=False,
        postgresql_where=sa.text("status = 'published'"),
    )

    # --- notice_attachments ------------------------------------------------
    op.create_table(
        'notice_attachments',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('notice_id', sa.BigInteger(), nullable=False),
        sa.Column('vault_document_id', sa.BigInteger(), nullable=False),
        sa.Column('added_by', sa.BigInteger(), nullable=True),
        *_base_cols(),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.ForeignKeyConstraint(['notice_id'], ['notices.id'], ),
        sa.ForeignKeyConstraint(['vault_document_id'], ['vault_documents.id'], ),
        sa.ForeignKeyConstraint(['added_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_notice_attachments_notice', 'notice_attachments',
        ['notice_id'], unique=False,
    )

    # --- notice_reads ------------------------------------------------------
    op.create_table(
        'notice_reads',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('notice_id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=False),
        *_base_cols(),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.ForeignKeyConstraint(['notice_id'], ['notices.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_notice_reads_notice_user', 'notice_reads',
        ['notice_id', 'user_id'], unique=True,
    )
    op.create_index(
        'ix_notice_reads_notice', 'notice_reads', ['notice_id'], unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_notice_reads_notice', table_name='notice_reads')
    op.drop_index('uq_notice_reads_notice_user', table_name='notice_reads')
    op.drop_table('notice_reads')
    op.drop_index('ix_notice_attachments_notice', table_name='notice_attachments')
    op.drop_table('notice_attachments')
    op.drop_index('ix_notices_active_feed', table_name='notices')
    op.drop_index('ix_notices_society_status_pinned_published', table_name='notices')
    op.drop_table('notices')

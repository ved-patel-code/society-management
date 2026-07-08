"""complaints module

Revision ID: 0006_complaints
Revises: 0005_finance
Create Date: 2026-07-08 00:00:00.000000+00:00

Adds the five Complaints tables (docs/modules/complaints.md §3):
complaint_categories, complaint_reference_counters, complaints,
complaint_status_history, complaint_images. The DB holds ONLY integrity
constraints (PK/FK/NOT NULL/UNIQUE) + the composite/partial indexes each feature's
common query needs (docs/03 §5); all business rules (transition table, image caps,
ownership, reference allocation) live in the service layer.

Two partial indexes: the active-category unique name (uniqueness only among active
rows, so a deactivated name frees up) and the auto-archive scan (closed complaints
by close date). Tables are created in FK-dependency order; downgrade drops in
reverse (indexes first). No FK cascade (append-only convention).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0006_complaints'
down_revision: Union[str, None] = '0005_finance'
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
    # --- complaint_categories ---------------------------------------------
    op.create_table(
        'complaint_categories',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('is_system', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        *_base_cols(),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_complaint_categories_society_active_name',
        'complaint_categories', ['society_id', 'name'], unique=True,
        postgresql_where=sa.text('is_active = true'),
    )
    op.create_index(
        'ix_complaint_categories_society_active',
        'complaint_categories', ['society_id', 'is_active'], unique=False,
    )

    # --- complaint_reference_counters -------------------------------------
    op.create_table(
        'complaint_reference_counters',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('next_value', sa.BigInteger(), server_default='0', nullable=False),
        *_base_cols(),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_complaint_reference_counters_society',
        'complaint_reference_counters', ['society_id'], unique=True,
    )

    # --- complaints --------------------------------------------------------
    op.create_table(
        'complaints',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('reference', sa.String(length=16), nullable=False),
        sa.Column('house_id', sa.BigInteger(), nullable=False),
        sa.Column('raised_by', sa.BigInteger(), nullable=False),
        sa.Column('category_id', sa.BigInteger(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), server_default='open', nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('withdrawn_at', sa.DateTime(timezone=True), nullable=True),
        *_base_cols(),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.ForeignKeyConstraint(['house_id'], ['houses.id'], ),
        sa.ForeignKeyConstraint(['raised_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['category_id'], ['complaint_categories.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_complaints_society_reference', 'complaints',
        ['society_id', 'reference'], unique=True,
    )
    op.create_index('ix_complaints_society_status', 'complaints', ['society_id', 'status'], unique=False)
    op.create_index('ix_complaints_society_house', 'complaints', ['society_id', 'house_id'], unique=False)
    op.create_index('ix_complaints_society_category', 'complaints', ['society_id', 'category_id'], unique=False)
    op.create_index(
        'ix_complaints_status_closed_at', 'complaints', ['status', 'closed_at'],
        unique=False, postgresql_where=sa.text("status = 'closed'"),
    )

    # --- complaint_status_history -----------------------------------------
    op.create_table(
        'complaint_status_history',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('complaint_id', sa.BigInteger(), nullable=False),
        sa.Column('from_status', sa.String(length=16), nullable=True),
        sa.Column('to_status', sa.String(length=16), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('changed_by', sa.BigInteger(), nullable=True),
        *_base_cols(),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.ForeignKeyConstraint(['complaint_id'], ['complaints.id'], ),
        sa.ForeignKeyConstraint(['changed_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_complaint_status_history_complaint_created',
        'complaint_status_history', ['complaint_id', 'created_at'], unique=False,
    )

    # --- complaint_images --------------------------------------------------
    op.create_table(
        'complaint_images',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('complaint_id', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.String(length=8), nullable=False),
        sa.Column('vault_document_id', sa.BigInteger(), nullable=False),
        sa.Column('added_by', sa.BigInteger(), nullable=True),
        *_base_cols(),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.ForeignKeyConstraint(['complaint_id'], ['complaints.id'], ),
        sa.ForeignKeyConstraint(['vault_document_id'], ['vault_documents.id'], ),
        sa.ForeignKeyConstraint(['added_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_complaint_images_complaint_kind', 'complaint_images',
        ['complaint_id', 'kind'], unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_complaint_images_complaint_kind', table_name='complaint_images')
    op.drop_table('complaint_images')
    op.drop_index('ix_complaint_status_history_complaint_created', table_name='complaint_status_history')
    op.drop_table('complaint_status_history')
    op.drop_index('ix_complaints_status_closed_at', table_name='complaints')
    op.drop_index('ix_complaints_society_category', table_name='complaints')
    op.drop_index('ix_complaints_society_house', table_name='complaints')
    op.drop_index('ix_complaints_society_status', table_name='complaints')
    op.drop_index('uq_complaints_society_reference', table_name='complaints')
    op.drop_table('complaints')
    op.drop_index('uq_complaint_reference_counters_society', table_name='complaint_reference_counters')
    op.drop_table('complaint_reference_counters')
    op.drop_index('ix_complaint_categories_society_active', table_name='complaint_categories')
    op.drop_index('uq_complaint_categories_society_active_name', table_name='complaint_categories')
    op.drop_table('complaint_categories')

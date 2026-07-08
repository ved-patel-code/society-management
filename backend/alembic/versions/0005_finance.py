"""finance module

Revision ID: 0005_finance
Revises: 0004_vault
Create Date: 2026-07-08 00:00:00.000000+00:00

Adds the eight Finance tables (docs/modules/finance.md §3): maintenance_rates,
house_dues, payments, payment_allocations, prepaid_blocks, expense_categories,
expenses, ledger_entries. Money is NUMERIC(12,2) throughout. The DB holds ONLY
integrity constraints (PK/FK/NOT NULL/UNIQUE) + the composite indexes each
feature's common query needs (docs/03 §5); all business rules live in the service
layer.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0005_finance'
down_revision: Union[str, None] = '0004_vault'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MONEY = sa.Numeric(12, 2)


def _base_cols() -> list:
    """The columns every table carries (Base): id PK + created_at/updated_at."""
    return [
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    ]


def upgrade() -> None:
    # --- maintenance_rates -------------------------------------------------
    op.create_table(
        'maintenance_rates',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('amount', _MONEY, nullable=False),
        sa.Column('valid_from', sa.Date(), nullable=False),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        *_base_cols(),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_maintenance_rates_society_valid_from',
        'maintenance_rates', ['society_id', 'valid_from'], unique=True,
    )
    op.create_index(
        'ix_maintenance_rates_society_valid_from',
        'maintenance_rates', ['society_id', 'valid_from'], unique=False,
    )

    # --- house_dues --------------------------------------------------------
    op.create_table(
        'house_dues',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('house_id', sa.BigInteger(), nullable=False),
        sa.Column('period_year', sa.Integer(), nullable=False),
        sa.Column('period_month', sa.Integer(), nullable=False),
        sa.Column('amount_due', _MONEY, nullable=False),
        sa.Column('due_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(length=16), server_default='outstanding', nullable=False),
        sa.Column('source', sa.String(length=16), server_default='accrued', nullable=False),
        sa.Column('locked_rate', _MONEY, nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        *_base_cols(),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.ForeignKeyConstraint(['house_id'], ['houses.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_house_dues_house_period', 'house_dues',
        ['society_id', 'house_id', 'period_year', 'period_month'], unique=True,
    )
    op.create_index(
        'ix_house_dues_society_status', 'house_dues',
        ['society_id', 'status'], unique=False,
    )
    op.create_index(
        'ix_house_dues_house_status', 'house_dues',
        ['house_id', 'status'], unique=False,
    )

    # --- payments ----------------------------------------------------------
    op.create_table(
        'payments',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('house_id', sa.BigInteger(), nullable=False),
        sa.Column('amount', _MONEY, nullable=False),
        sa.Column('method', sa.String(length=16), nullable=False),
        sa.Column('reference', sa.String(length=255), nullable=True),
        sa.Column('provider', sa.String(length=16), server_default='admin_manual', nullable=False),
        sa.Column('provider_ref', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='recorded', nullable=False),
        sa.Column('recorded_by', sa.BigInteger(), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('voided_by', sa.BigInteger(), nullable=True),
        sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('void_reason', sa.Text(), nullable=True),
        *_base_cols(),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.ForeignKeyConstraint(['house_id'], ['houses.id'], ),
        sa.ForeignKeyConstraint(['recorded_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['voided_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_payments_society_house', 'payments', ['society_id', 'house_id'], unique=False)
    op.create_index('ix_payments_society_status', 'payments', ['society_id', 'status'], unique=False)

    # --- payment_allocations ----------------------------------------------
    op.create_table(
        'payment_allocations',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('payment_id', sa.BigInteger(), nullable=False),
        sa.Column('house_due_id', sa.BigInteger(), nullable=False),
        sa.Column('amount_applied', _MONEY, nullable=False),
        *_base_cols(),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.ForeignKeyConstraint(['payment_id'], ['payments.id'], ),
        sa.ForeignKeyConstraint(['house_due_id'], ['house_dues.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_payment_allocations_payment', 'payment_allocations', ['payment_id'], unique=False)
    op.create_index('ix_payment_allocations_house_due', 'payment_allocations', ['house_due_id'], unique=False)

    # --- prepaid_blocks ----------------------------------------------------
    op.create_table(
        'prepaid_blocks',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('house_id', sa.BigInteger(), nullable=False),
        sa.Column('months_count', sa.Integer(), nullable=False),
        sa.Column('rate_locked', _MONEY, nullable=False),
        sa.Column('payment_id', sa.BigInteger(), nullable=False),
        sa.Column('start_period', sa.Integer(), nullable=False),
        sa.Column('end_period', sa.Integer(), nullable=False),
        *_base_cols(),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.ForeignKeyConstraint(['house_id'], ['houses.id'], ),
        sa.ForeignKeyConstraint(['payment_id'], ['payments.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_prepaid_blocks_society_house', 'prepaid_blocks', ['society_id', 'house_id'], unique=False)

    # --- expense_categories ------------------------------------------------
    op.create_table(
        'expense_categories',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('is_system', sa.Boolean(), server_default='false', nullable=False),
        *_base_cols(),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_expense_categories_society_name', 'expense_categories',
        ['society_id', 'name'], unique=True,
    )

    # --- expenses ----------------------------------------------------------
    op.create_table(
        'expenses',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('category_id', sa.BigInteger(), nullable=False),
        sa.Column('amount', _MONEY, nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('incurred_on', sa.Date(), nullable=False),
        sa.Column('recorded_by', sa.BigInteger(), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='recorded', nullable=False),
        sa.Column('voided_by', sa.BigInteger(), nullable=True),
        sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('void_reason', sa.Text(), nullable=True),
        *_base_cols(),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.ForeignKeyConstraint(['category_id'], ['expense_categories.id'], ),
        sa.ForeignKeyConstraint(['recorded_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['voided_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_expenses_society_incurred', 'expenses', ['society_id', 'incurred_on'], unique=False)
    op.create_index('ix_expenses_society_category', 'expenses', ['society_id', 'category_id'], unique=False)

    # --- ledger_entries ----------------------------------------------------
    op.create_table(
        'ledger_entries',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('entry_type', sa.String(length=24), nullable=False),
        sa.Column('direction', sa.String(length=8), nullable=False),
        sa.Column('amount', _MONEY, nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('occurred_on', sa.Date(), nullable=False),
        sa.Column('source_type', sa.String(length=16), nullable=True),
        sa.Column('source_id', sa.BigInteger(), nullable=True),
        sa.Column('recorded_by', sa.BigInteger(), nullable=True),
        sa.Column('reverses_entry_id', sa.BigInteger(), nullable=True),
        sa.Column('is_reversed', sa.Boolean(), server_default='false', nullable=False),
        *_base_cols(),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.ForeignKeyConstraint(['recorded_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['reverses_entry_id'], ['ledger_entries.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ledger_entries_society_occurred', 'ledger_entries', ['society_id', 'occurred_on'], unique=False)
    op.create_index('ix_ledger_entries_reverses', 'ledger_entries', ['reverses_entry_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_ledger_entries_reverses', table_name='ledger_entries')
    op.drop_index('ix_ledger_entries_society_occurred', table_name='ledger_entries')
    op.drop_table('ledger_entries')
    op.drop_index('ix_expenses_society_category', table_name='expenses')
    op.drop_index('ix_expenses_society_incurred', table_name='expenses')
    op.drop_table('expenses')
    op.drop_index('uq_expense_categories_society_name', table_name='expense_categories')
    op.drop_table('expense_categories')
    op.drop_index('ix_prepaid_blocks_society_house', table_name='prepaid_blocks')
    op.drop_table('prepaid_blocks')
    op.drop_index('ix_payment_allocations_house_due', table_name='payment_allocations')
    op.drop_index('ix_payment_allocations_payment', table_name='payment_allocations')
    op.drop_table('payment_allocations')
    op.drop_index('ix_payments_society_status', table_name='payments')
    op.drop_index('ix_payments_society_house', table_name='payments')
    op.drop_table('payments')
    op.drop_index('ix_house_dues_house_status', table_name='house_dues')
    op.drop_index('ix_house_dues_society_status', table_name='house_dues')
    op.drop_index('uq_house_dues_house_period', table_name='house_dues')
    op.drop_table('house_dues')
    op.drop_index('ix_maintenance_rates_society_valid_from', table_name='maintenance_rates')
    op.drop_index('uq_maintenance_rates_society_valid_from', table_name='maintenance_rates')
    op.drop_table('maintenance_rates')

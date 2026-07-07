"""house & occupancy tables

Revision ID: 0003_house_occupancy
Revises: 0002_onboarding
Create Date: 2026-07-07 00:00:00.000000+00:00

Adds the two module-owned tables (house_occupancies, house_status_history). The
shared ``houses`` table already has ``status`` + ``first_left_empty_on`` (created
by 0002) — this migration NEVER touches ``houses``.

``house_occupancies.id_proof_document_id`` is a plain BIGINT with NO foreign key:
``vault_documents`` does not exist yet. The Vault module's migration will add the
FK later (docs/modules/house-occupancy.md §3/§7).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0003_house_occupancy'
down_revision: Union[str, None] = '0002_onboarding'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'house_occupancies',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('house_id', sa.BigInteger(), nullable=False),
        sa.Column('party_type', sa.String(length=16), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=True),
        sa.Column('full_name', sa.String(length=255), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=True),
        sa.Column('contact_number', sa.String(length=32), nullable=True),
        sa.Column('persons_living', sa.Integer(), nullable=True),
        sa.Column('id_proof_type', sa.Text(), nullable=True),
        # Nullable, NO FK yet — vault_documents does not exist (Vault TODO).
        sa.Column('id_proof_document_id', sa.BigInteger(), nullable=True),
        sa.Column('is_current', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('valid_from', sa.Date(), nullable=False),
        sa.Column('valid_to', sa.Date(), nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['house_id'], ['houses.id'], ),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_house_occupancies_society_house', 'house_occupancies', ['society_id', 'house_id'], unique=False)
    op.create_index('ix_house_occupancies_user', 'house_occupancies', ['user_id'], unique=False)
    op.create_index('uq_house_occupancy_current', 'house_occupancies', ['house_id', 'party_type'], unique=True, postgresql_where=sa.text('is_current = true'))

    op.create_table(
        'house_status_history',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('house_id', sa.BigInteger(), nullable=False),
        sa.Column('from_status', sa.String(length=16), nullable=False),
        sa.Column('to_status', sa.String(length=16), nullable=False),
        sa.Column('changed_by', sa.BigInteger(), nullable=True),
        sa.Column('changed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['changed_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['house_id'], ['houses.id'], ),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_house_status_history_society_house', 'house_status_history', ['society_id', 'house_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_house_status_history_society_house', table_name='house_status_history')
    op.drop_table('house_status_history')
    op.drop_index('uq_house_occupancy_current', table_name='house_occupancies', postgresql_where=sa.text('is_current = true'))
    op.drop_index('ix_house_occupancies_user', table_name='house_occupancies')
    op.drop_index('ix_house_occupancies_society_house', table_name='house_occupancies')
    op.drop_table('house_occupancies')

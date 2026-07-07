"""vault module

Revision ID: 0004_vault
Revises: 0003_house_occupancy
Create Date: 2026-07-07 00:00:00.000000+00:00

Adds the three Vault tables (vault_folders, vault_documents,
society_storage_usage) and finally wires the deferred foreign key from
``house_occupancies.id_proof_document_id`` → ``vault_documents.id`` (ON DELETE
SET NULL) — the link House & Occupancy left as a bare BIGINT until Vault existed
(docs/modules/house-occupancy.md §3/§7, docs/modules/vault.md §3).

``vault_folders.notice_id`` stays a bare BIGINT (no FK): the ``notices`` table
does not exist until the Notice Board module is built.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0004_vault'
down_revision: Union[str, None] = '0003_house_occupancy'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- vault_folders (self-referential parent_id) ------------------------
    op.create_table(
        'vault_folders',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('parent_id', sa.BigInteger(), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('is_system', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('system_key', sa.String(length=32), nullable=True),
        sa.Column('house_id', sa.BigInteger(), nullable=True),
        # No FK: notices table does not exist yet (Notice Board TODO).
        sa.Column('notice_id', sa.BigInteger(), nullable=True),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.ForeignKeyConstraint(['parent_id'], ['vault_folders.id'], ),
        sa.ForeignKeyConstraint(['house_id'], ['houses.id'], ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_vault_folders_parent_name',
        'vault_folders',
        ['society_id', 'parent_id', 'name'],
        unique=True,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
    op.create_index(
        'ix_vault_folders_society_parent',
        'vault_folders',
        ['society_id', 'parent_id'],
        unique=False,
    )
    op.create_index('ix_vault_folders_house', 'vault_folders', ['house_id'], unique=False)

    # --- vault_documents ---------------------------------------------------
    op.create_table(
        'vault_documents',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('folder_id', sa.BigInteger(), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=255), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('storage_key', sa.String(length=1024), nullable=False),
        sa.Column('checksum', sa.String(length=64), nullable=True),
        sa.Column('source', sa.String(length=16), server_default='manual', nullable=False),
        sa.Column('source_ref', sa.BigInteger(), nullable=True),
        sa.Column('uploaded_by', sa.BigInteger(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_by', sa.BigInteger(), nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.ForeignKeyConstraint(['folder_id'], ['vault_folders.id'], ),
        sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['deleted_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_vault_documents_society_folder',
        'vault_documents',
        ['society_id', 'folder_id'],
        unique=False,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
    op.create_index(
        'ix_vault_documents_deleted_at', 'vault_documents', ['deleted_at'], unique=False
    )
    op.create_index(
        'uq_vault_documents_storage_key', 'vault_documents', ['storage_key'], unique=True
    )

    # --- society_storage_usage --------------------------------------------
    op.create_table(
        'society_storage_usage',
        sa.Column('society_id', sa.BigInteger(), nullable=False),
        sa.Column('used_bytes', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['society_id'], ['societies.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_society_storage_usage_society',
        'society_storage_usage',
        ['society_id'],
        unique=True,
    )

    # --- wire the deferred House & Occupancy ID-proof FK -------------------
    op.create_foreign_key(
        'fk_house_occupancies_id_proof_document',
        'house_occupancies',
        'vault_documents',
        ['id_proof_document_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(
        'fk_house_occupancies_id_proof_document',
        'house_occupancies',
        type_='foreignkey',
    )
    op.drop_index('uq_society_storage_usage_society', table_name='society_storage_usage')
    op.drop_table('society_storage_usage')
    op.drop_index('uq_vault_documents_storage_key', table_name='vault_documents')
    op.drop_index('ix_vault_documents_deleted_at', table_name='vault_documents')
    op.drop_index(
        'ix_vault_documents_society_folder',
        table_name='vault_documents',
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
    op.drop_table('vault_documents')
    op.drop_index('ix_vault_folders_house', table_name='vault_folders')
    op.drop_index('ix_vault_folders_society_parent', table_name='vault_folders')
    op.drop_index(
        'uq_vault_folders_parent_name',
        table_name='vault_folders',
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
    op.drop_table('vault_folders')

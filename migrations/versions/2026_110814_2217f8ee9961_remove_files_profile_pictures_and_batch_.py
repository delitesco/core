"""remove files profile pictures and batch import

Revision ID: 2217f8ee9961
Revises: bc5aed40052b
Create Date: 2026-11-08 14:55:24.902927

"""
import sqlalchemy_utils
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '2217f8ee9961'
down_revision = 'bc5aed40052b'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index('ix_users_profile_picture_id', table_name='users')
    op.drop_constraint('users_profile_picture_id_fkey', 'users', type_='foreignkey')
    op.drop_column('users', 'profile_picture_id')
    op.drop_constraint('alias_batch_import_id_fkey', 'alias', type_='foreignkey')
    op.drop_column('alias', 'batch_import_id')
    op.drop_index('ix_batch_import_file_id', table_name='batch_import')
    op.drop_index('ix_batch_import_user_id', table_name='batch_import')
    op.drop_table('batch_import')
    op.drop_index('ix_file_user_id', table_name='file')
    op.drop_table('file')


def downgrade():
    op.create_table('file',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.Column('path', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='file_user_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='file_pkey'),
    sa.UniqueConstraint('path', name='file_path_key')
    )
    op.create_index('ix_file_user_id', 'file', ['user_id'], unique=False)
    op.create_table('batch_import',
    sa.Column('id', sa.INTEGER(), server_default=sa.text("nextval('batch_import_id_seq'::regclass)"), autoincrement=True, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.Column('user_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('file_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('processed', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('summary', sa.TEXT(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['file_id'], ['file.id'], name='batch_import_file_id_fkey', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='batch_import_user_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='batch_import_pkey'),
    postgresql_ignore_search_path=False
    )
    op.create_index('ix_batch_import_user_id', 'batch_import', ['user_id'], unique=False)
    op.create_index('ix_batch_import_file_id', 'batch_import', ['file_id'], unique=False)
    op.add_column('alias', sa.Column('batch_import_id', sa.INTEGER(), autoincrement=False, nullable=True))
    op.create_foreign_key('alias_batch_import_id_fkey', 'alias', 'batch_import', ['batch_import_id'], ['id'], ondelete='SET NULL')
    op.add_column('users', sa.Column('profile_picture_id', sa.INTEGER(), autoincrement=False, nullable=True))
    op.create_foreign_key('users_profile_picture_id_fkey', 'users', 'file', ['profile_picture_id'], ['id'])
    op.create_index('ix_users_profile_picture_id', 'users', ['profile_picture_id'], unique=False)

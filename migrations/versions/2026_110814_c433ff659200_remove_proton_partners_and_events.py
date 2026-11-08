"""remove proton partners and events

Revision ID: c433ff659200
Revises: a7f9f0c1f903
Create Date: 2026-11-08 14:30:12.614441

"""
import sqlalchemy_utils
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c433ff659200'
down_revision = 'a7f9f0c1f903'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('custom_domain_partner_id_fkey', 'custom_domain', type_='foreignkey')
    op.drop_column('custom_domain', 'partner_id')
    op.drop_constraint('public_domain_partner_id_fkey', 'public_domain', type_='foreignkey')
    op.drop_column('public_domain', 'partner_id')
    op.drop_index('ix_partner_subscription_end_at', table_name='partner_subscription')
    op.drop_table('partner_subscription')
    op.drop_index('ix_partner_user_user_id', table_name='partner_user')
    op.drop_table('partner_user')
    op.drop_index('ix_partner_api_token_partner_id', table_name='partner_api_token')
    op.drop_index('ix_partner_api_token_token', table_name='partner_api_token')
    op.drop_table('partner_api_token')
    op.drop_table('partner')
    op.drop_index('ix_sync_event_created_at', table_name='sync_event')
    op.drop_index('ix_sync_event_taken_time', table_name='sync_event')
    op.drop_table('sync_event')


def downgrade():
    op.create_table('sync_event',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.Column('content', postgresql.BYTEA(), autoincrement=False, nullable=False),
    sa.Column('taken_time', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.Column('retry_count', sa.INTEGER(), server_default=sa.text('0'), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name='sync_event_pkey')
    )
    op.create_index('ix_sync_event_taken_time', 'sync_event', ['taken_time'], unique=False)
    op.create_index('ix_sync_event_created_at', 'sync_event', ['created_at'], unique=False)
    op.create_table('partner',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.Column('name', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('contact_email', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name='partner_pkey'),
    sa.UniqueConstraint('contact_email', name='partner_contact_email_key'),
    sa.UniqueConstraint('name', name='partner_name_key')
    )
    op.create_table('partner_api_token',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.Column('token', sa.VARCHAR(length=50), autoincrement=False, nullable=False),
    sa.Column('partner_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('expiration_time', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['partner_id'], ['partner.id'], name='partner_api_token_partner_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='partner_api_token_pkey')
    )
    op.create_index('ix_partner_api_token_token', 'partner_api_token', ['token'], unique=True)
    op.create_index('ix_partner_api_token_partner_id', 'partner_api_token', ['partner_id'], unique=False)
    op.create_table('partner_user',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.Column('user_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('partner_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('partner_email', sa.VARCHAR(length=255), autoincrement=False, nullable=True),
    sa.Column('external_user_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['partner_id'], ['partner.id'], name='partner_user_partner_id_fkey', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='partner_user_user_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='partner_user_pkey'),
    sa.UniqueConstraint('partner_id', 'external_user_id', name='uq_partner_id_external_user_id')
    )
    op.create_index('ix_partner_user_user_id', 'partner_user', ['user_id'], unique=True)
    op.create_table('partner_subscription',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.Column('partner_user_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('end_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.Column('lifetime', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['partner_user_id'], ['partner_user.id'], name='partner_subscription_partner_user_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='partner_subscription_pkey'),
    sa.UniqueConstraint('partner_user_id', name='partner_subscription_partner_user_id_key')
    )
    op.create_index('ix_partner_subscription_end_at', 'partner_subscription', ['end_at'], unique=False)
    op.add_column('custom_domain', sa.Column('partner_id', sa.INTEGER(), autoincrement=False, nullable=True))
    op.create_foreign_key('custom_domain_partner_id_fkey', 'custom_domain', 'partner', ['partner_id'], ['id'])
    op.add_column('public_domain', sa.Column('partner_id', sa.INTEGER(), autoincrement=False, nullable=True))
    op.create_foreign_key('public_domain_partner_id_fkey', 'public_domain', 'partner', ['partner_id'], ['id'], ondelete='CASCADE')

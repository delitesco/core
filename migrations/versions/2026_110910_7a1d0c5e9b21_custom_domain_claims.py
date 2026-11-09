"""custom domain: unique only once ownership is verified

A domain was unique across all users from its first, unverified claim, so anyone
could lock its owner out by adding it first. It stays unique among the domains whose
ownership is verified (ix_unique_domain) and per user.

Revision ID: 7a1d0c5e9b21
Revises: be64f469a875
Create Date: 2026-11-09 10:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '7a1d0c5e9b21'
down_revision = 'be64f469a875'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('custom_domain_domain_key', 'custom_domain', type_='unique')
    op.create_index('ix_custom_domain_domain', 'custom_domain', ['domain'], unique=False)
    op.create_index('ix_custom_domain_user_id_domain', 'custom_domain', ['user_id', 'domain'], unique=True)


def downgrade():
    op.drop_index('ix_custom_domain_user_id_domain', table_name='custom_domain')
    op.drop_index('ix_custom_domain_domain', table_name='custom_domain')
    op.create_unique_constraint('custom_domain_domain_key', 'custom_domain', ['domain'])

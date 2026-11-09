"""user setting block_trackers

Revision ID: 9c3e5b7d2f40
Revises: 7a1d0c5e9b21
Create Date: 2026-11-09 11:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9c3e5b7d2f40'
down_revision = '7a1d0c5e9b21'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('block_trackers', sa.Boolean(), server_default='0', nullable=False))


def downgrade():
    op.drop_column('users', 'block_trackers')

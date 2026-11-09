"""alias block_trackers (null: follow the user's setting)

Revision ID: b4e8a1c6d903
Revises: 9c3e5b7d2f40
Create Date: 2026-11-09 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b4e8a1c6d903'
down_revision = '9c3e5b7d2f40'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('alias', sa.Column('block_trackers', sa.Boolean(), nullable=True))


def downgrade():
    op.drop_column('alias', 'block_trackers')

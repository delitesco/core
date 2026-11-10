"""user replies_blocked_at

Revision ID: c7d2e9f4a185
Revises: b4e8a1c6d903
Create Date: 2026-11-10 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import sqlalchemy_utils


# revision identifiers, used by Alembic.
revision = 'c7d2e9f4a185'
down_revision = 'b4e8a1c6d903'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'users',
        sa.Column('replies_blocked_at', sqlalchemy_utils.types.arrow.ArrowType(), nullable=True),
    )


def downgrade():
    op.drop_column('users', 'replies_blocked_at')

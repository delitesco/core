"""store API keys as hashes

Revision ID: d5a3f8b2c614
Revises: c7d2e9f4a185
Create Date: 2026-11-12 21:40:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "d5a3f8b2c614"
down_revision = "c7d2e9f4a185"
branch_labels = None
depends_on = None


def upgrade():
    # existing keys keep working: the API hashes the key it is given the same way
    op.execute(
        "UPDATE api_key SET code = encode(sha256(convert_to(code, 'UTF8')), 'hex')"
    )


def downgrade():
    # a hash cannot be turned back into its key: rotate the keys through provisioning
    pass

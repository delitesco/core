"""remove subscriptions and payments

Revision ID: 75acfe0dc122
Revises: ec54c4bb2081
Create Date: 2026-11-08 14:46:20.990083

"""
import sqlalchemy_utils
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '75acfe0dc122'
down_revision = 'ec54c4bb2081'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index('ix_users_activated_trial_end_lifetime', table_name='users')
    op.drop_index('ix_users_referral_id', table_name='users')
    op.drop_constraint('users_lifetime_coupon_id_fkey', 'users', type_='foreignkey')
    op.drop_constraint('users_referral_id_fkey', 'users', type_='foreignkey')
    op.drop_column('users', 'lifetime_coupon_id')
    op.drop_column('users', 'trial_end')
    op.drop_column('users', 'paid_lifetime')
    op.drop_column('users', 'referral_id')
    op.drop_column('users', 'lifetime')
    op.drop_table('lifetime_coupon')
    op.drop_index('ix_referral_user_id', table_name='referral')
    op.drop_table('referral')
    op.drop_table('manual_subscription')
    op.drop_table('subscription')
    op.drop_table('apple_subscription')
    op.drop_index('ix_payout_user_id', table_name='payout')
    op.drop_table('payout')
    op.drop_index('ix_coupon_used_by_user_id', table_name='coupon')
    op.drop_table('coupon')
    op.drop_table('coinbase_subscription')
    # the plan enums belong to the dropped tables; downgrade recreates them
    op.execute("DROP TYPE IF EXISTS planenum2")
    op.execute("DROP TYPE IF EXISTS planenum_apple")


def downgrade():
    op.create_table('coinbase_subscription',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.Column('user_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('end_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('code', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='coinbase_subscription_user_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='coinbase_subscription_pkey'),
    sa.UniqueConstraint('user_id', name='coinbase_subscription_user_id_key')
    )
    op.create_table('coupon',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.Column('code', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('nb_year', sa.INTEGER(), server_default=sa.text('1'), autoincrement=False, nullable=False),
    sa.Column('used', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False),
    sa.Column('used_by_user_id', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('is_giveaway', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False),
    sa.Column('comment', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('expires_date', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['used_by_user_id'], ['users.id'], name='coupon_used_by_user_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='coupon_pkey'),
    sa.UniqueConstraint('code', name='coupon_code_key')
    )
    op.create_index('ix_coupon_used_by_user_id', 'coupon', ['used_by_user_id'], unique=False)
    op.create_table('payout',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.Column('user_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('amount', postgresql.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=False),
    sa.Column('payment_method', sa.VARCHAR(length=256), autoincrement=False, nullable=False),
    sa.Column('number_upgraded_account', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('comment', sa.TEXT(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='payout_user_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='payout_pkey')
    )
    op.create_index('ix_payout_user_id', 'payout', ['user_id'], unique=False)
    op.create_table('apple_subscription',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.Column('user_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('expires_date', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('original_transaction_id', sa.VARCHAR(length=256), autoincrement=False, nullable=False),
    sa.Column('receipt_data', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('plan', postgresql.ENUM('monthly', 'yearly', name='planenum_apple'), autoincrement=False, nullable=False),
    sa.Column('product_id', sa.VARCHAR(length=256), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='apple_subscription_user_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='apple_subscription_pkey'),
    sa.UniqueConstraint('original_transaction_id', name='apple_subscription_original_transaction_id_key'),
    sa.UniqueConstraint('user_id', name='apple_subscription_user_id_key')
    )
    op.create_table('subscription',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.Column('cancel_url', sa.VARCHAR(length=1024), autoincrement=False, nullable=False),
    sa.Column('update_url', sa.VARCHAR(length=1024), autoincrement=False, nullable=False),
    sa.Column('subscription_id', sa.VARCHAR(length=1024), autoincrement=False, nullable=False),
    sa.Column('event_time', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('next_bill_date', sa.DATE(), autoincrement=False, nullable=False),
    sa.Column('cancelled', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('plan', postgresql.ENUM('monthly', 'yearly', name='planenum2'), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='subscription_user_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='subscription_pkey'),
    sa.UniqueConstraint('subscription_id', name='subscription_subscription_id_key'),
    sa.UniqueConstraint('user_id', name='subscription_user_id_key')
    )
    op.create_table('manual_subscription',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.Column('user_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('end_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('comment', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('is_giveaway', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='manual_subscription_user_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='manual_subscription_pkey'),
    sa.UniqueConstraint('user_id', name='manual_subscription_user_id_key')
    )
    op.create_table('referral',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.Column('user_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('code', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('name', sa.VARCHAR(length=512), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='referral_user_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='referral_pkey'),
    sa.UniqueConstraint('code', name='referral_code_key')
    )
    op.create_index('ix_referral_user_id', 'referral', ['user_id'], unique=False)
    op.create_table('lifetime_coupon',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.Column('code', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('nb_used', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('paid', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False),
    sa.Column('comment', sa.TEXT(), autoincrement=False, nullable=True),
    sa.PrimaryKeyConstraint('id', name='lifetime_coupon_pkey'),
    sa.UniqueConstraint('code', name='lifetime_coupon_code_key')
    )
    op.add_column('users', sa.Column('lifetime', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False))
    op.add_column('users', sa.Column('referral_id', sa.INTEGER(), autoincrement=False, nullable=True))
    op.add_column('users', sa.Column('paid_lifetime', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False))
    op.add_column('users', sa.Column('trial_end', postgresql.TIMESTAMP(), autoincrement=False, nullable=True))
    op.add_column('users', sa.Column('lifetime_coupon_id', sa.INTEGER(), autoincrement=False, nullable=True))
    op.create_foreign_key('users_referral_id_fkey', 'users', 'referral', ['referral_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('users_lifetime_coupon_id_fkey', 'users', 'lifetime_coupon', ['lifetime_coupon_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_users_referral_id', 'users', ['referral_id'], unique=False)
    op.create_index('ix_users_activated_trial_end_lifetime', 'users', ['activated', 'trial_end', 'lifetime'], unique=False)

"""v2i_a send suppression

V2-I-a — the legal/privacy send-suppression prerequisite (docs/PROGRESS.md's
carried-forward BLOCKING `claimed_email` note, first recorded at V2-DH).
Purely additive: one new table (`email_suppressions`, the GLOBAL/cross-
prospect suppression list keyed by `normalize_email_identity(...)`) and four
new nullable columns on `contact_channels` (LOCAL suppression metadata for
the EMAIL channel). Nothing existing is altered, renamed, or dropped.

Revision ID: 94f688f37818
Revises: 1ec5eceed8d4
Create Date: 2026-09-08 03:51:51.669846

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '94f688f37818'
down_revision: Union[str, Sequence[str], None] = '1ec5eceed8d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('email_suppressions',
    sa.Column('identity_key', sa.String(), nullable=False),
    sa.Column('reason', sa.String(), nullable=False),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('provider_code', sa.String(), nullable=True),
    sa.Column('first_observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('observed_prospect_id', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['observed_prospect_id'], ['prospects.id'], ),
    sa.PrimaryKeyConstraint('identity_key')
    )
    # `batch_alter_table` (mirrors the V2-B migration's own precedent):
    # `op.drop_column` in `downgrade()` below has no direct SQLite equivalent
    # (SQLite lacks `ALTER TABLE DROP COLUMN` pre-3.35 and Alembic's SQLite
    # dialect never emits it directly) — batch mode uses the copy-and-move
    # strategy on SQLite and plain ALTER statements on every other dialect,
    # so one migration body stays correct on both, including under
    # `test_migration_drift.py`'s unconditional SQLite `upgrade head` run.
    with op.batch_alter_table('contact_channels', schema=None) as batch_op:
        batch_op.add_column(sa.Column('send_suppressed_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('send_suppression_reason', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('send_suppression_source', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('send_suppression_provider_code', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('contact_channels', schema=None) as batch_op:
        batch_op.drop_column('send_suppression_provider_code')
        batch_op.drop_column('send_suppression_source')
        batch_op.drop_column('send_suppression_reason')
        batch_op.drop_column('send_suppressed_at')
    op.drop_table('email_suppressions')

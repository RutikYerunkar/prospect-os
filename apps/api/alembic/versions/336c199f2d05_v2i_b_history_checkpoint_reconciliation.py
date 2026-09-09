"""v2i_b history checkpoint reconciliation

V2-I-b correction (post-smoke) — one additive, nullable column:
`action_executions.pre_dispatch_history_id`. Stores Gmail's `historyId`
(`users.getProfile`), captured and persisted BEFORE `messages.send`, as the
new load-bearing reconciliation anchor — the real V2-I-b smoke send proved
Gmail can omit our generated `message_id_header` from both `Message-ID` and
`X-Google-Original-Message-ID`, so the prior Message-ID-based reconciliation
contract could not be trusted. Stored as `String` (never a fixed-width
integer) — Gmail serializes `historyId` as an opaque, unboundedly-growing
decimal string, and this column preserves it losslessly. NULL for every
execution created before this correction. Nothing existing is altered,
renamed, or dropped.

Revision ID: 336c199f2d05
Revises: 2384e7ddd94e
Create Date: 2026-09-09 04:23:03.756926

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '336c199f2d05'
down_revision: Union[str, Sequence[str], None] = '2384e7ddd94e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # `batch_alter_table` (mirrors the V2-I-a migration's own precedent):
    # `op.drop_column` in `downgrade()` below has no direct SQLite
    # equivalent — batch mode uses the copy-and-move strategy on SQLite and
    # a plain ALTER statement on every other dialect, so one migration body
    # stays correct on both, including under `test_migration_drift.py`'s
    # unconditional SQLite `upgrade head` run.
    with op.batch_alter_table('action_executions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pre_dispatch_history_id', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('action_executions', schema=None) as batch_op:
        batch_op.drop_column('pre_dispatch_history_id')

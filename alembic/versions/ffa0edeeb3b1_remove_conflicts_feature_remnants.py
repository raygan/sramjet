"""Remove conflicts feature remnants

Drops the orphaned `conflicts` table (feature replaced by last-write-wins)
and the `had_conflicts` column on `sync_events` (never set after removal).

Revision ID: ffa0edeeb3b1
Revises: f901f3dace41
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'ffa0edeeb3b1'
down_revision: Union[str, Sequence[str], None] = 'f901f3dace41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop orphaned conflicts table — only if it exists, since fresh installs
    # created by the initial migration never had it.
    conn = op.get_bind()
    tables = {r[0] for r in conn.execute(
        sa.text("SELECT name FROM sqlite_master WHERE type='table'")
    ).fetchall()}
    if 'conflicts' in tables:
        op.drop_table('conflicts')

    # Drop had_conflicts from sync_events.
    # SQLite requires batch mode for column removal.
    with op.batch_alter_table('sync_events') as batch_op:
        batch_op.drop_column('had_conflicts')


def downgrade() -> None:
    with op.batch_alter_table('sync_events') as batch_op:
        batch_op.add_column(sa.Column(
            'had_conflicts', sa.Boolean(), nullable=False, server_default='0'
        ))
    # conflicts table not recreated — feature was intentionally removed

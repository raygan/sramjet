"""Add pin columns to versions

Adds is_pinned and pin_note to the versions table to support the
pinned saves/states feature. Pinned versions are kept indefinitely
regardless of retention limit settings.

Revision ID: 284fc1246ab0
Revises: 940982df4de5
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '284fc1246ab0'
down_revision: Union[str, Sequence[str], None] = '940982df4de5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('versions') as batch_op:
        batch_op.add_column(sa.Column('is_pinned', sa.Boolean(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('pin_note', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('versions') as batch_op:
        batch_op.drop_column('pin_note')
        batch_op.drop_column('is_pinned')

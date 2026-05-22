"""Move file-based device state to database

Replaces flat JSON files under devices/{name}/ with database columns
and a new table:

  force_accept flag     → devices.force_accept_at (nullable DateTime)
  quarantine.json       → devices.quarantine_saves / quarantine_states (Boolean)
  quarantine_canonical  → devices.quarantine_canonical_json (Text)
  last_fetched_manifest → device_file_fetches table

Data migration: reads existing JSON files from DATA_DIR/devices/ and
populates the new columns. Missing files are silently skipped.

Revision ID: 940982df4de5
Revises: ffa0edeeb3b1
Create Date: 2026-05-21
"""
from typing import Sequence, Union
import json
import os

import sqlalchemy as sa
from alembic import op

revision: str = '940982df4de5'
down_revision: Union[str, Sequence[str], None] = 'ffa0edeeb3b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Schema changes ────────────────────────────────────────────────────────

    with op.batch_alter_table('devices') as batch_op:
        batch_op.add_column(sa.Column('force_accept_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('quarantine_saves', sa.Boolean(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('quarantine_states', sa.Boolean(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('quarantine_canonical_json', sa.Text(), nullable=True))

    op.create_table(
        'device_file_fetches',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('device_id', sa.Integer(), sa.ForeignKey('devices.id'), nullable=False, index=True),
        sa.Column('file_path', sa.String(), nullable=False),
        sa.Column('hash', sa.String(), nullable=False),
        sa.UniqueConstraint('device_id', 'file_path'),
    )

    # ── Data migration ────────────────────────────────────────────────────────

    conn = op.get_bind()
    devices_dir = _devices_dir()
    if not devices_dir or not os.path.isdir(devices_dir):
        return  # nothing to migrate

    devices = conn.execute(sa.text("SELECT id, name FROM devices")).fetchall()

    for device_id, device_name in devices:
        device_path = os.path.join(devices_dir, device_name)
        if not os.path.isdir(device_path):
            continue

        # Quarantine settings
        q_path = os.path.join(device_path, "quarantine.json")
        if os.path.exists(q_path):
            try:
                q = json.loads(open(q_path).read())
                conn.execute(
                    sa.text(
                        "UPDATE devices SET quarantine_saves = :saves, quarantine_states = :states"
                        " WHERE id = :id"
                    ),
                    {"saves": 1 if q.get("saves") else 0,
                     "states": 1 if q.get("states") else 0,
                     "id": device_id},
                )
            except (ValueError, OSError):
                pass

        # Quarantine canonical manifest
        qc_path = os.path.join(device_path, "quarantine_canonical.json")
        if os.path.exists(qc_path):
            try:
                qc_json = open(qc_path).read()
                json.loads(qc_json)  # validate
                conn.execute(
                    sa.text("UPDATE devices SET quarantine_canonical_json = :json WHERE id = :id"),
                    {"json": qc_json, "id": device_id},
                )
            except (ValueError, OSError):
                pass

        # Last fetched manifest → device_file_fetches rows
        lf_path = os.path.join(device_path, "last_fetched_manifest.json")
        if os.path.exists(lf_path):
            try:
                entries = json.loads(open(lf_path).read())
                for entry in entries:
                    path = entry.get("path")
                    hash_val = entry.get("hash")
                    if path and hash_val:
                        conn.execute(
                            sa.text(
                                "INSERT OR IGNORE INTO device_file_fetches"
                                " (device_id, file_path, hash) VALUES (:did, :fp, :h)"
                            ),
                            {"did": device_id, "fp": path, "h": hash_val},
                        )
            except (ValueError, OSError):
                pass


def downgrade() -> None:
    op.drop_table('device_file_fetches')
    with op.batch_alter_table('devices') as batch_op:
        batch_op.drop_column('quarantine_canonical_json')
        batch_op.drop_column('quarantine_states')
        batch_op.drop_column('quarantine_saves')
        batch_op.drop_column('force_accept_at')


def _devices_dir() -> str | None:
    """Return the DATA_DIR/devices path from the environment, or None."""
    data_dir = os.environ.get("DATA_DIR")
    if not data_dir:
        return None
    return os.path.join(data_dir, "devices")

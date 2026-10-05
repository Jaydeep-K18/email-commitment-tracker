"""Remember what Google last accepted for each commitment.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05

Google pushes move into individual background jobs. Each records the hash of the
event body Google accepted, so the next sync only queues a push for commitments
whose content actually changed. Existing rows start NULL, which reads as "never
pushed with a hash", so each gets exactly one reconciling push.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("commitments") as batch:
        batch.add_column(sa.Column("gcal_synced_hash", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("commitments") as batch:
        batch.drop_column("gcal_synced_hash")

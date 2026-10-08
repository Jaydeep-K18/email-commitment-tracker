"""Drop the Streamlit dashboard's "seen" flag on emails.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08

The dashboard announced new VIP email by flipping ``raw_emails.notification_seen``.
Notifications now come from the event log (the ``notifications`` table, written
by the server's notifier), and the dashboard is gone, so nothing reads or writes
the column any more.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("raw_emails") as batch:
        batch.drop_column("notification_seen")


def downgrade() -> None:
    with op.batch_alter_table("raw_emails") as batch:
        batch.add_column(
            sa.Column("notification_seen", sa.Boolean(), nullable=False, server_default=sa.true())
        )

"""Baseline: the four v1 tables, as they stood before the full-stack rebuild.

Revision ID: 0001
Revises:
Create Date: 2026-10-05

Server-side defaults are added here even though the Python models use client
side ones: the Node server inserts into some of these tables (VIP contacts, for
one) and must not have to restate every default the worker already knows.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def utc_now() -> sa.TextClause:
    """A server default producing naive UTC, matching ``utcnow_naive``.

    On Postgres, plain ``now()`` is a timestamptz; stored into a timestamp
    column it is converted using the *session's* time zone. Converting
    explicitly makes the stored value UTC whatever the connection's setting.
    """
    if op.get_context().dialect.name == "postgresql":
        return sa.text("(now() at time zone 'utc')")
    return sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.create_table(
        "raw_emails",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=True),
        sa.Column("sender_email", sa.String(), nullable=True),
        sa.Column("sender_name", sa.String(), nullable=True),
        sa.Column("recipient_email", sa.String(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column("vip_tier", sa.String(), nullable=True),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "notification_seen", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("fetched_at", sa.DateTime(), nullable=False, server_default=utc_now()),
    )
    op.create_index("ix_raw_emails_message_id", "raw_emails", ["message_id"], unique=True)

    op.create_table(
        "vip_contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("match_value", sa.String(), nullable=False),
        sa.Column("match_type", sa.String(), nullable=False),
        sa.Column("tier", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.UniqueConstraint("match_value", "match_type", name="uq_vip_value_type"),
    )
    op.create_index("ix_vip_contacts_match_value", "vip_contacts", ["match_value"])

    op.create_table(
        "commitments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "email_id",
            sa.Integer(),
            sa.ForeignKey("raw_emails.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("deadline", sa.DateTime(), nullable=True),
        sa.Column("counterparty_name", sa.String(), nullable=True),
        sa.Column("counterparty_email", sa.String(), nullable=True),
        sa.Column("direction", sa.String(), nullable=True),
        sa.Column("evidence_quote", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("vip_tier", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column(
            "calendar_synced", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("ics_uid", sa.String(), nullable=True),
        sa.Column("gcal_event_id", sa.String(), nullable=True),
        sa.Column(
            "manually_added", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "sync_approved", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "supersedes_id",
            sa.Integer(),
            sa.ForeignKey("commitments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=utc_now()),
    )
    op.create_index("ix_commitments_email_id", "commitments", ["email_id"])
    op.create_index("ix_commitments_type", "commitments", ["type"])

    op.create_table(
        "sync_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "commitment_id",
            sa.Integer(),
            sa.ForeignKey("commitments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="success"),
        sa.Column("synced_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_sync_log_commitment_id", "sync_log", ["commitment_id"])


def downgrade() -> None:
    op.drop_table("sync_log")
    op.drop_table("commitments")
    op.drop_table("vip_contacts")
    op.drop_table("raw_emails")

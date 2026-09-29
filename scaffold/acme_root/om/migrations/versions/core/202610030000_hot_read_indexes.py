"""Indexes that fit the hot reads: the `mine` scope, the done list split by
shelf, the idempotency, invitations, and Slack purges; and the three indexes
no statement needs, dropped or narrowed.

Revision ID: 202610030000
Revises: 202610020001
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610030000"
down_revision = "202610020001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610030000_hot_read_indexes.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610030000_hot_read_indexes.down.sql")

"""The queued items, in the order they become ready, across lanes.

Revision ID: 202610080000
Revises: 202610060001
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610080000"
down_revision = "202610060001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202610080000_oldest_ready_index.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202610080000_oldest_ready_index.down.sql")

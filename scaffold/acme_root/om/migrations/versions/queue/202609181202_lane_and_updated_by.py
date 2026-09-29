"""The lane, and updated_by, on work items.

Revision ID: 202609181202
Revises: 202609160957
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609181202"
down_revision = "202609160957"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609181202_lane_and_updated_by.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609181202_lane_and_updated_by.down.sql")

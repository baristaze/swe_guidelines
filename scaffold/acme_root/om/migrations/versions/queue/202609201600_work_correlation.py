"""The request that caused a work item and that request's trace context, so
the run names its cause and links to its trace.

Revision ID: 202609201600
Revises: 202609192000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609201600"
down_revision = "202609192000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609201600_work_correlation.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609201600_work_correlation.down.sql")

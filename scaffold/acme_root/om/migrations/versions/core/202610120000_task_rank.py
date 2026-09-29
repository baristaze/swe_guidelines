"""An open task's place is an exact decimal rank, filled from its position;
the position stays, kept by a trigger for the release before (ADR 0050).

Revision ID: 202610120000
Revises: 202610030000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610120000"
down_revision = "202610030000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610120000_task_rank.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610120000_task_rank.down.sql")

"""The position leaves the mapping; a trigger keeps it the rank's float for
the release before, which still reads it, and its index goes (ADR 0050).

Revision ID: 202610200000
Revises: 202610170000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610200000"
down_revision = "202610170000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610200000_task_position_unmapped.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610200000_task_position_unmapped.down.sql")

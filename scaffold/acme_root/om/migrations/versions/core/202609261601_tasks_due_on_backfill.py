"""Every task with a due time takes its UTC date as the due date; the
downgrade gives the release before a due time on each due date.

Revision ID: 202609261601
Revises: 202609261600
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609261601"
down_revision = "202609261600"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609261601_tasks_due_on_backfill.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609261601_tasks_due_on_backfill.down.sql")

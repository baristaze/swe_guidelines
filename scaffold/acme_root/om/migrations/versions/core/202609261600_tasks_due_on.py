"""A task's due date, and the time zone a person's reminders are timed in.
The expand half: `tasks.remind_at` stays for the release before this one.

Revision ID: 202609261600
Revises: 202609261400
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609261600"
down_revision = "202609261400"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609261600_tasks_due_on.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609261600_tasks_due_on.down.sql")

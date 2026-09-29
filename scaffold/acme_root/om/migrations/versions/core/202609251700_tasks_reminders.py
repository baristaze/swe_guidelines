"""Reminders on tasks: the due time a person sets, and when its reminder went
out.

Revision ID: 202609251700
Revises: 202609250001
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609251700"
down_revision = "202609250001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609251700_tasks_reminders.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609251700_tasks_reminders.down.sql")

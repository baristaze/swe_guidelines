"""The open tasks newest first: the index the short list Slack shows reads.

Revision ID: 202609261700
Revises: 202609261601
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609261700"
down_revision = "202609261601"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609261700_tasks_recent.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609261700_tasks_recent.down.sql")

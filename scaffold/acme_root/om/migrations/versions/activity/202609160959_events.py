"""The events table, the first of the activity role.

Revision ID: 202609160959
Revises: None
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609160959"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202609160959_events.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202609160959_events.down.sql")

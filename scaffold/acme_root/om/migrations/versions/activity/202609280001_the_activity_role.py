"""The activity role, whole: the event stream and its cursors, with their
fences and the serving logins' grants.

Revision ID: 202609280001
Revises: None
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609280001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202609280001_the_activity_role.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202609280001_the_activity_role.down.sql")

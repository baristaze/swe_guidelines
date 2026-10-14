"""The activity role, whole: the change event stream and its cursors, each
with its fence, and the serving logins' grants.

Revision ID: 202610142241
Revises: None
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610142241"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610142241_the_activity_role.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610142241_the_activity_role.down.sql")

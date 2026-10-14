"""The queue role, whole: the work items and the tenants' caps, each with its
fence, and the serving logins' grants.

Revision ID: 202610141707
Revises: None
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141707"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202610141707_the_queue_role.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202610141707_the_queue_role.down.sql")

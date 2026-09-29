"""The queue role, whole: the work items, with the fence of each login and the
serving logins' grants.

Revision ID: 202609280002
Revises: None
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609280002"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609280002_the_queue_role.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609280002_the_queue_role.down.sql")

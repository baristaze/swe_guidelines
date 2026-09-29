"""The system scope of every queue policy admits the system login alone,
and, for one release, the login every process used before.

Revision ID: 202609221201
Revises: 202609221200
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609221201"
down_revision = "202609221200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609221201_system_login_fence.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609221201_system_login_fence.down.sql")

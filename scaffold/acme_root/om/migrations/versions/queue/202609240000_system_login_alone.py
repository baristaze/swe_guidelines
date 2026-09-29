"""The system scope of every queue policy admits the system login alone.

Revision ID: 202609240000
Revises: 202609230000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609240000"
down_revision = "202609230000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609240000_system_login_alone.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609240000_system_login_alone.down.sql")

"""The runtime and the system logins hold DML on every table of the
core role, now and to come, and own none of it.

Revision ID: 202609221200
Revises: 202609221000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609221200"
down_revision = "202609221000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609221200_login_grants.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609221200_login_grants.down.sql")

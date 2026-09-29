"""Row-level security as the second fence: every tenant table of the
core role holds one policy, and the tenant comes from the transaction.

Revision ID: 202609202000
Revises: 202609201600
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609202000"
down_revision = "202609201600"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609202000_row_level_security.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609202000_row_level_security.down.sql")

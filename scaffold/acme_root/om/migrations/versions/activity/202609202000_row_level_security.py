"""Row-level security as the second fence: every tenant table of the
activity role holds one policy, and the tenant comes from the transaction.

Revision ID: 202609202000
Revises: 202609200930
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609202000"
down_revision = "202609200930"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202609202000_row_level_security.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202609202000_row_level_security.down.sql")

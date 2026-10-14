"""Each not-null constraint of a renamed table takes its table's name.

Revision ID: 202610142241
Revises: 202610141704
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610142241"
down_revision = "202610141704"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ADMIN, "202610142241_the_not_null_constraints_take_their_tables_name.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ADMIN, "202610142241_the_not_null_constraints_take_their_tables_name.down.sql")

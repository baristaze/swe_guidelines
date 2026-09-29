"""The contract half of ADR 0038: the three dead identity columns and
`tasks.remind_at` leave the tables. The release before this one names none of
them.

Revision ID: 202609290000
Revises: 202609280000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609290000"
down_revision = "202609280000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609290000_drop_the_dead_columns.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609290000_drop_the_dead_columns.down.sql")

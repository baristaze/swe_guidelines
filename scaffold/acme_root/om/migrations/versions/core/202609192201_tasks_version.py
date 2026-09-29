"""Optimistic concurrency on tasks: the version column the compare-and-set
conditions every update, move, and soft delete on.

Revision ID: 202609192201
Revises: 202609192200
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609192201"
down_revision = "202609192200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609192201_tasks_version.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609192201_tasks_version.down.sql")

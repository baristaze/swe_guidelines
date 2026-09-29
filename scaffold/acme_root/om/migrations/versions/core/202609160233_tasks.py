"""The tasks table.

Revision ID: 202609160233
Revises: 202609160146
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609160233"
down_revision = "202609160146"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609160233_tasks.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609160233_tasks.down.sql")

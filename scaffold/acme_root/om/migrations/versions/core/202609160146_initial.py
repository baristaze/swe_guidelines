"""The tenancy tables.

Revision ID: 202609160146
Revises: None
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609160146"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609160146_initial.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609160146_initial.down.sql")

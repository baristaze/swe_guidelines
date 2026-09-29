"""The done tasks not archived, by their last change, across tenants: the
index the sweep's read of the tenants with a chore due walks for the
archivable ones.

Revision ID: 202610170000
Revises: 202610150000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610170000"
down_revision = "202610150000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610170000_chore_tenants_index.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610170000_chore_tenants_index.down.sql")

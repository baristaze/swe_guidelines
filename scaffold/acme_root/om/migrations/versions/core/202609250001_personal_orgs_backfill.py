"""Every existing person gets their personal org.

Revision ID: 202609250001
Revises: 202609250000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609250001"
down_revision = "202609250000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609250001_personal_orgs_backfill.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609250001_personal_orgs_backfill.down.sql")

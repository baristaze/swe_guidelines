"""`orgs.purged_at`: a deleted tenant the sweep found nothing left of, which
it leaves out from then on.

Revision ID: 202610020001
Revises: 202610020000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610020001"
down_revision = "202610020000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020001_orgs_purged_at.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020001_orgs_purged_at.down.sql")

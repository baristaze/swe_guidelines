"""The size tally is the system's: `admin.platform_sizes` becomes
`admin.system_sizes`, its row and its key kept.

Revision ID: 202610141700
Revises: 202609280003
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141700"
down_revision = "202609280003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ADMIN, "202610141700_the_size_tally_is_the_systems.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ADMIN, "202610141700_the_size_tally_is_the_systems.down.sql")

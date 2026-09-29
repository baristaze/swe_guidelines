"""The purges across tenants: an index under each, led by the column its
retention is counted on, and the three indexes only the per-tenant purges
read, dropped, with the fence's that the attempt's index now serves.

Revision ID: 202610140000
Revises: 202610120000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610140000"
down_revision = "202610120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610140000_retention_indexes.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610140000_retention_indexes.down.sql")

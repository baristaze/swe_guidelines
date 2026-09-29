"""Long-running orchestrations under the tenant fence, and when the cleanup
archived a done task.

Revision ID: 202609300000
Revises: 202609290000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609300000"
down_revision = "202609290000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609300000_orchestrations.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609300000_orchestrations.down.sql")

"""The transactional outbox table.

Revision ID: 202609181200
Revises: 202609181000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609181200"
down_revision = "202609181000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609181200_outbox_rows.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609181200_outbox_rows.down.sql")

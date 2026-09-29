"""The sweep's claim on outbox rows: attempts, the next attempt, the last
error, and the dead-letter mark.

Revision ID: 202609192001
Revises: 202609191600
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609192001"
down_revision = "202609191600"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609192001_outbox_attempts.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609192001_outbox_attempts.down.sql")

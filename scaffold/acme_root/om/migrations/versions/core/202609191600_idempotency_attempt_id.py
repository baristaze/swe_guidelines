"""The idempotency record carries the token of the attempt that holds the
marker, so finish and release are conditional on it.

Revision ID: 202609191600
Revises: 202609191500
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609191600"
down_revision = "202609191500"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609191600_idempotency_attempt_id.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609191600_idempotency_attempt_id.down.sql")

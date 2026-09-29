"""A release keeps the idempotency record and clears only its attempt, so
the retry that follows a failure reruns on the record's target id.

Revision ID: 202609192200
Revises: 202609192100
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609192200"
down_revision = "202609192100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609192200_idempotency_released_marker.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609192200_idempotency_released_marker.down.sql")

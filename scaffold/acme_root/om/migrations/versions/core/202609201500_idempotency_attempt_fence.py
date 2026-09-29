"""The index the re-mint's fence reads: (org_id, target_id) on the markers,
since the fence asks for the marker on one target and the unique index beside
it leads with the caller's key.

Revision ID: 202609201500
Revises: 202609201400
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609201500"
down_revision = "202609201400"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609201500_idempotency_attempt_fence.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609201500_idempotency_attempt_fence.down.sql")

"""An index under every purge statement the sweep runs: the outbox's dead
letters, deleted tasks, sessions and socket tickets by expiry, idempotency
records and Slack posts by birth.

Revision ID: 202610020000
Revises: 202609300000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610020000"
down_revision = "202609300000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020000_purge_indexes.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020000_purge_indexes.down.sql")

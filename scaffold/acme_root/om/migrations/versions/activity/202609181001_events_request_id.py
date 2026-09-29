"""The request id on every event.

Revision ID: 202609181001
Revises: 202609160959
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609181001"
down_revision = "202609160959"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202609181001_events_request_id.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202609181001_events_request_id.down.sql")

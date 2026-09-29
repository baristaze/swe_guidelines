"""The trace context of the request behind an outbox row, for the link the
far side of a handoff raises.

Revision ID: 202609201600
Revises: 202609201500
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609201600"
down_revision = "202609201500"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609201600_outbox_traceparent.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609201600_outbox_traceparent.down.sql")

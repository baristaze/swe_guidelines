"""The stream is change events: `activity.events` becomes
`activity.change_events` and `activity.event_cursors` becomes
`activity.change_event_cursors`, every row kept.

Revision ID: 202610141703
Revises: 202610141702
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141703"
down_revision = "202610141702"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610141703_the_stream_is_change_events.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610141703_the_stream_is_change_events.down.sql")
